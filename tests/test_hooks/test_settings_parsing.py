"""settings.json hook parsing: nested & flat shapes, matcher scoping."""

from __future__ import annotations

from claude_code.hooks.events import HookConfig, HookEvent, HookPayload
from claude_code.hooks.manager import HookManager


def test_nested_shape_parses() -> None:
    # The shape Claude Code writes — outer matcher + inner hooks list.
    cfg = HookConfig.from_settings({
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "check.sh"},
            ]},
        ],
    })
    entries = cfg.hooks["PreToolUse"]
    assert len(entries) == 1
    assert entries[0].command == "check.sh"
    assert entries[0].matcher == "Bash"


def test_flat_shape_still_parses() -> None:
    cfg = HookConfig.from_settings({
        "PreToolUse": [{"command": "check.sh", "priority": 10}, "lint.sh"],
    })
    entries = cfg.hooks["PreToolUse"]
    assert [e.command for e in entries] == ["check.sh", "lint.sh"]
    assert entries[0].priority == 10


def test_nested_without_matcher() -> None:
    cfg = HookConfig.from_settings({
        "PostToolUse": [{"hooks": [{"type": "command", "command": "fmt.sh"}]}],
    })
    assert cfg.hooks["PostToolUse"][0].command == "fmt.sh"
    assert cfg.hooks["PostToolUse"][0].matcher == ""


def test_malformed_entries_skipped_not_raised() -> None:
    # Missing command / junk entries must be dropped, not raise.
    cfg = HookConfig.from_settings({
        "PreToolUse": [
            {"matcher": "X", "hooks": [{"type": "command"}]},  # no command
            {"foo": "bar"},                                     # no command
            {"matcher": "Y", "hooks": [{"command": "ok.sh"}]},  # valid
        ],
    })
    assert [e.command for e in cfg.hooks["PreToolUse"]] == ["ok.sh"]


def test_manager_loads_nested_config() -> None:
    # HookManager(config) is where the pydantic error used to surface.
    mgr = HookManager({
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "c.sh"}]},
        ],
    })
    bucket = mgr._hooks.get(HookEvent.PRE_TOOL_USE, [])
    assert len(bucket) == 1


async def test_matcher_scopes_to_tool() -> None:
    mgr = HookManager({
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "exit 2"}]},
        ],
    })
    # Non-Bash tool: matcher doesn't match → hook skipped → allowed.
    allowed = await mgr.fire(
        HookEvent.PRE_TOOL_USE,
        HookPayload(event=HookEvent.PRE_TOOL_USE, tool_name="Read"),
    )
    assert allowed.decision.value == "allow"
    # Bash tool: matcher matches → hook runs → exit 2 denies.
    denied = await mgr.fire(
        HookEvent.PRE_TOOL_USE,
        HookPayload(event=HookEvent.PRE_TOOL_USE, tool_name="Bash"),
    )
    assert denied.decision.value == "deny"


async def test_matcher_regex_semantics() -> None:
    # Claude Code matchers are regex: "Edit|Write", ".*", "Notebook.*".
    from claude_code.hooks.events import HookEvent, HookPayload
    from claude_code.hooks.manager import HookManager

    mgr = HookManager({
        "PreToolUse": [
            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "exit 2"}]},
        ],
    })
    # "Edit" matches the alternation → hook runs → deny.
    denied = await mgr.fire(
        HookEvent.PRE_TOOL_USE,
        HookPayload(event=HookEvent.PRE_TOOL_USE, tool_name="Edit"),
    )
    assert denied.decision.value == "deny"
    # "Bash" doesn't match → hook skipped → allow.
    allowed = await mgr.fire(
        HookEvent.PRE_TOOL_USE,
        HookPayload(event=HookEvent.PRE_TOOL_USE, tool_name="Bash"),
    )
    assert allowed.decision.value == "allow"


def test_malformed_hook_entry_does_not_crash() -> None:
    from claude_code.hooks.manager import HookManager

    # Non-string matcher / non-coercible priority must be skipped, not crash.
    mgr = HookManager({
        "PreToolUse": [
            {"matcher": 123, "hooks": [{"type": "command", "command": "ok.sh"}]},
            {"command": "bad.sh", "priority": "high"},
            {"command": "good.sh"},
        ],
    })
    from claude_code.hooks.events import HookEvent
    cmds = [e.command for _, e in mgr._hooks.get(HookEvent.PRE_TOOL_USE, [])]
    assert "good.sh" in cmds
