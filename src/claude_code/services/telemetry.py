"""Anonymized usage telemetry with local buffering.

Events are stored locally in ``~/.claude/telemetry/`` as daily JSONL files,
rotated daily with a maximum 30-day retention window.  No personal data
(code, file paths, prompts) is ever recorded — only aggregate counters
and timing information.

Telemetry can be disabled via ``CLAUDE_TELEMETRY=false`` or the
``enabled`` parameter.
"""

from __future__ import annotations

import datetime
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from claude_code.utils.logging import get_logger
from claude_code.utils.path_utils import expand_user

logger = get_logger("services.telemetry")

# Retention: how many days of log files to keep
_MAX_RETENTION_DAYS = 30

# Maximum buffered events before auto-flush
_BUFFER_FLUSH_THRESHOLD = 100


@dataclass
class TelemetryEvent:
    """A single telemetry record."""

    event_type: str
    timestamp: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)


class TelemetryManager:
    """Collect anonymized usage data.

    Parameters
    ----------
    enabled:
        Whether telemetry collection is active.  When ``False``, all
        ``record_*`` calls become no-ops.
    session_id:
        Identifier for the current session (for grouping events).
    storage_dir:
        Override for the telemetry directory.  Defaults to
        ``~/.claude/telemetry/``.
    """

    def __init__(
        self,
        enabled: bool = True,
        session_id: str = "",
        storage_dir: Optional[Path] = None,
    ) -> None:
        self._enabled = enabled and os.environ.get("CLAUDE_TELEMETRY", "").lower() != "false"
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._storage_dir = storage_dir or expand_user("~/.claude/telemetry")
        self._buffer: list[TelemetryEvent] = []

        if self._enabled:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_old_files()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return whether telemetry is currently active."""
        return self._enabled

    def record_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Record a generic telemetry event.

        Args:
            event_type: A short identifier (e.g. ``"session_start"``).
            data: Arbitrary key-value payload (must not contain PII).
        """
        if not self._enabled:
            return
        event = TelemetryEvent(
            event_type=event_type,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            session_id=self._session_id,
            data=data,
        )
        self._buffer.append(event)
        if len(self._buffer) >= _BUFFER_FLUSH_THRESHOLD:
            self.flush()

    def record_tool_use(self, tool_name: str, duration_ms: float, success: bool) -> None:
        """Record a tool invocation.

        Args:
            tool_name: Name of the tool used.
            duration_ms: Execution time in milliseconds.
            success: Whether the tool completed without error.
        """
        self.record_event("tool_use", {
            "tool": tool_name,
            "duration_ms": round(duration_ms, 2),
            "success": success,
        })

    def record_conversation_turn(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage for a conversation turn.

        Args:
            input_tokens: Prompt tokens consumed.
            output_tokens: Completion tokens produced.
        """
        self.record_event("conversation_turn", {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    def record_error(self, error_type: str, message: str) -> None:
        """Record an error occurrence.

        Args:
            error_type: Error category (e.g. ``"provider_error"``).
            message: Sanitized error message (no PII).
        """
        self.record_event("error", {
            "error_type": error_type,
            "message": message[:500],  # Truncate to avoid oversized records
        })

    def get_summary(self) -> dict[str, Any]:
        """Return an aggregate summary of buffered events.

        Returns:
            Dict with event counts, total tokens, and tool usage stats.
        """
        summary: dict[str, Any] = {
            "session_id": self._session_id,
            "enabled": self._enabled,
            "buffered_events": len(self._buffer),
            "event_counts": {},
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "tool_calls": 0,
            "errors": 0,
        }

        for event in self._buffer:
            summary["event_counts"][event.event_type] = (
                summary["event_counts"].get(event.event_type, 0) + 1
            )
            if event.event_type == "conversation_turn":
                summary["total_input_tokens"] += event.data.get("input_tokens", 0)
                summary["total_output_tokens"] += event.data.get("output_tokens", 0)
            elif event.event_type == "tool_use":
                summary["tool_calls"] += 1
            elif event.event_type == "error":
                summary["errors"] += 1

        return summary

    def flush(self) -> None:
        """Write buffered events to the daily log file.

        Events are appended to a JSONL file named by date
        (``YYYY-MM-DD.jsonl``) in the telemetry storage directory.
        """
        if not self._enabled or not self._buffer:
            return

        today = datetime.date.today().isoformat()
        log_path = self._storage_dir / f"{today}.jsonl"

        try:
            with log_path.open("a", encoding="utf-8") as fh:
                for event in self._buffer:
                    line = json.dumps({
                        "event_type": event.event_type,
                        "timestamp": event.timestamp,
                        "session_id": event.session_id,
                        "data": event.data,
                    }, separators=(",", ":"))
                    fh.write(line + "\n")
            logger.debug(f"Flushed {len(self._buffer)} telemetry events to {log_path}")
        except OSError as exc:
            logger.warning(f"Failed to flush telemetry: {exc}")

        self._buffer.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cleanup_old_files(self) -> None:
        """Remove telemetry log files older than the retention period."""
        cutoff = datetime.date.today() - datetime.timedelta(days=_MAX_RETENTION_DAYS)
        try:
            for path in self._storage_dir.glob("*.jsonl"):
                try:
                    file_date_str = path.stem  # e.g. "2025-06-01"
                    file_date = datetime.date.fromisoformat(file_date_str)
                    if file_date < cutoff:
                        path.unlink(missing_ok=True)
                        logger.debug(f"Removed old telemetry file: {path}")
                except ValueError:
                    # Non-date filename, skip
                    continue
        except OSError as exc:
            logger.warning(f"Telemetry cleanup failed: {exc}")
