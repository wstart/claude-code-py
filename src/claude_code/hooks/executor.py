"""Execute hook scripts/commands as subprocesses."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any

from .events import HookDecision, HookPayload, HookResult

logger = logging.getLogger(__name__)

# Exit codes with special meaning
_EXIT_ALLOW = 0
_EXIT_DENY = 2


class HookExecutor:
    """Execute hook commands and parse their output into HookResults.

    A hook command receives the :class:`HookPayload` as JSON on stdin and
    communicates its decision via stdout (JSON) and exit code:

    * Exit 0 + valid JSON ``{"decision": "deny", ...}`` -> parsed result.
    * Exit 0 + non-JSON stdout -> ALLOW with stdout as reason.
    * Exit 0 + empty stdout -> ALLOW.
    * Exit 2 -> DENY (stdout used as reason).
    * Any other exit code -> ALLOW, unless ``fail_closed`` (then DENY).
    * Timeout / start failure -> ALLOW, unless ``fail_closed`` (then DENY).

    ``fail_closed`` is for hooks that gate a security-sensitive action
    (e.g. PreToolUse): if the hook can't run to a clean decision, the
    action is blocked rather than silently permitted.
    """

    def __init__(self, working_directory: str = "") -> None:
        self.working_directory = working_directory or os.getcwd()

    async def execute(
        self,
        command: str,
        payload: HookPayload,
        timeout: float = 30.0,
        fail_closed: bool = False,
    ) -> HookResult:
        """Run a hook command and return its decision.

        Args:
            command: Shell command or path to a script file.
            payload: The event payload to pass via stdin.
            timeout: Maximum seconds to wait for the hook.
            fail_closed: If True, an execution failure (start error, timeout,
                communication error, or unexpected exit code) yields DENY
                instead of ALLOW. Use for security-gating hooks.

        Returns:
            A HookResult reflecting the hook's decision.
        """
        stdin_data = payload.model_dump_json()
        fail_decision = HookDecision.DENY if fail_closed else HookDecision.ALLOW

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_directory,
                start_new_session=True,
            )
        except Exception as exc:
            logger.warning("Hook %r failed to start: %s", command, exc)
            return HookResult(decision=fail_decision, reason=f"Hook start error: {exc}")

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode()),
                timeout=timeout,
            )
        except TimeoutError:
            # Kill the whole process group so children of the shell die too.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError, AttributeError):
                pass
            proc.kill()
            await proc.wait()
            logger.warning("Hook %r timed out after %.1fs", command, timeout)
            return HookResult(
                decision=fail_decision,
                reason=f"Hook timed out after {timeout}s",
            )
        except Exception as exc:
            logger.warning("Hook %r communication error: %s", command, exc)
            return HookResult(decision=fail_decision, reason=f"Hook error: {exc}")

        stdout = stdout_bytes.decode(errors="replace").strip()
        stderr = stderr_bytes.decode(errors="replace").strip()
        exit_code = proc.returncode or 0

        if stderr:
            logger.debug("Hook %r stderr: %s", command, stderr)

        # Exit code 2 -> explicit DENY
        if exit_code == _EXIT_DENY:
            return HookResult(
                decision=HookDecision.DENY,
                reason=stdout or "Hook denied via exit code 2",
            )

        # Try parsing JSON from stdout
        parsed = _try_parse_json(stdout)
        if parsed is not None:
            return _result_from_dict(parsed)

        # Non-JSON stdout on a CLEAN exit -> ALLOW with text as reason.
        # A non-zero exit with non-JSON output must NOT short-circuit here,
        # or a crashed fail_closed hook (exit 1 + traceback) would be allowed.
        if stdout and exit_code == _EXIT_ALLOW:
            return HookResult(decision=HookDecision.ALLOW, reason=stdout)

        # Exit code 0 + empty stdout -> ALLOW
        if exit_code == _EXIT_ALLOW:
            return HookResult(decision=HookDecision.ALLOW)

        # Unexpected exit code -> ALLOW (or DENY under fail_closed)
        logger.warning(
            "Hook %r exited with code %d (stdout: %s)", command, exit_code, stdout[:200],
        )
        return HookResult(
            decision=fail_decision,
            reason=f"Hook exited with code {exit_code}",
        )


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Attempt to parse text as a JSON object. Returns None on failure."""
    if not text:
        return None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _result_from_dict(data: dict[str, Any]) -> HookResult:
    """Build a HookResult from a parsed JSON dict."""
    decision_str = data.get("decision", "allow")
    try:
        decision = HookDecision(decision_str)
    except ValueError:
        decision = HookDecision.ALLOW

    return HookResult(
        decision=decision,
        reason=data.get("reason", ""),
        modified_input=data.get("modified_input"),
        suppress_output=bool(data.get("suppress_output", False)),
    )
