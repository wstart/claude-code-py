"""HookEvent covers Claude Code's set; unknown events don't warn."""

from __future__ import annotations

import logging

from claude_code.hooks.events import HookEvent
from claude_code.hooks.manager import HookManager

# Every hook event Claude Code writes into settings.json must be recognised.
UPSTREAM_EVENTS = [
    "PreToolUse",
    "PostToolUse",
    "Notification",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "PreCompact",
    "SessionStart",
    "SessionEnd",
]


def test_all_upstream_events_recognised() -> None:
    for name in UPSTREAM_EVENTS:
        assert HookEvent(name).value == name


def test_known_events_load_without_warning(caplog) -> None:
    cfg = {
        name: [{"hooks": [{"type": "command", "command": "x"}]}]
        for name in UPSTREAM_EVENTS
    }
    with caplog.at_level(logging.WARNING, logger="claude_code.hooks.manager"):
        mgr = HookManager(cfg)
    assert caplog.records == []
    # SubagentStop / PreCompact (previously "unknown") now register.
    assert HookEvent.SUBAGENT_STOP in mgr._hooks
    assert HookEvent.PRE_COMPACT in mgr._hooks


def test_unknown_event_skipped_without_warning(caplog) -> None:
    cfg = {"Totally-Made-Up": [{"hooks": [{"type": "command", "command": "x"}]}]}
    with caplog.at_level(logging.WARNING, logger="claude_code.hooks.manager"):
        mgr = HookManager(cfg)
    assert caplog.records == []          # no WARNING noise
    assert mgr._hooks == {}              # and it's skipped, not loaded
