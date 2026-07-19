"""Telemetry — disabled (no-op stub)."""

from __future__ import annotations

from typing import Any


class TelemetryEvent:
    pass


class TelemetryManager:
    """No-op telemetry manager — all data collection disabled."""

    def __init__(self, enabled: bool = False) -> None:
        pass

    def record_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        pass

    def record_tool_use(self, tool_name: str, duration_ms: float = 0, success: bool = True) -> None:
        pass

    def record_conversation_turn(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        pass

    def record_error(self, error_type: str = "", message: str = "") -> None:
        pass

    def get_summary(self) -> dict[str, Any]:
        return {}

    def flush(self) -> None:
        pass

    def is_enabled(self) -> bool:
        return False
