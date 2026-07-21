"""Tests for hook fail-closed behavior on security-gating events."""

from __future__ import annotations

from claude_code.hooks.events import HookDecision, HookEvent, HookPayload
from claude_code.hooks.executor import HookExecutor
from claude_code.hooks.manager import HookEntry, HookManager


def _payload() -> HookPayload:
    return HookPayload(event=HookEvent.PRE_TOOL_USE, tool_name="Bash")


async def test_timeout_fail_open_by_default() -> None:
    ex = HookExecutor()
    r = await ex.execute("sleep 5", _payload(), timeout=0.2)
    assert r.decision == HookDecision.ALLOW


async def test_timeout_fail_closed() -> None:
    ex = HookExecutor()
    r = await ex.execute("sleep 5", _payload(), timeout=0.2, fail_closed=True)
    assert r.decision == HookDecision.DENY


async def test_bad_exit_code_fail_closed() -> None:
    ex = HookExecutor()
    r = await ex.execute("exit 7", _payload(), fail_closed=True)
    assert r.decision == HookDecision.DENY


async def test_crashed_hook_with_nonjson_stdout_fail_closed() -> None:
    # Security: a fail_closed hook that crashes (exit 1) while printing a
    # non-JSON traceback must DENY, not be short-circuited to ALLOW.
    ex = HookExecutor()
    r = await ex.execute("echo 'Traceback: boom'; exit 1", _payload(), fail_closed=True)
    assert r.decision == HookDecision.DENY


async def test_clean_nonjson_stdout_still_allows() -> None:
    # exit 0 + non-JSON stdout keeps the documented ALLOW contract.
    ex = HookExecutor()
    r = await ex.execute("echo ok; exit 0", _payload(), fail_closed=True)
    assert r.decision == HookDecision.ALLOW


async def test_clean_allow_respected_under_fail_closed() -> None:
    # A hook that runs and exits 0 (allow) is honoured even in fail-closed.
    ex = HookExecutor()
    r = await ex.execute("exit 0", _payload(), fail_closed=True)
    assert r.decision == HookDecision.ALLOW


async def test_explicit_deny_in_both_modes() -> None:
    ex = HookExecutor()
    assert (await ex.execute("exit 2", _payload())).decision == HookDecision.DENY
    assert (
        await ex.execute("exit 2", _payload(), fail_closed=True)
    ).decision == HookDecision.DENY


async def test_manager_pretooluse_is_fail_closed() -> None:
    # A crashing PreToolUse hook must block the tool.
    mgr = HookManager()
    mgr._hooks[HookEvent.PRE_TOOL_USE] = [
        (0, HookEntry(command="sleep 5", timeout=0.2, enabled=True))
    ]
    result = await mgr.fire(HookEvent.PRE_TOOL_USE, _payload())
    assert result.decision == HookDecision.DENY


async def test_manager_posttooluse_is_fail_open() -> None:
    # A crashing non-gating hook (PostToolUse) must not block.
    mgr = HookManager()
    mgr._hooks[HookEvent.POST_TOOL_USE] = [
        (0, HookEntry(command="sleep 5", timeout=0.2, enabled=True))
    ]
    payload = HookPayload(event=HookEvent.POST_TOOL_USE, tool_name="Bash")
    result = await mgr.fire(HookEvent.POST_TOOL_USE, payload)
    assert result.decision == HookDecision.ALLOW
