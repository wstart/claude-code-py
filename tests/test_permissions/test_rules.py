"""Tests for the permission rule engine and mode/risk classification."""

from __future__ import annotations

import pytest

from claude_code.permissions.modes import (
    PermissionMode,
    ToolRisk,
    get_risk_level,
    is_write_tool,
    needs_permission,
)
from claude_code.permissions.rules import (
    PermissionRule,
    RuleAction,
    RuleEngine,
)

# ---------------------------------------------------------------------------
# PermissionRule.parse
# ---------------------------------------------------------------------------


def test_parse_bare_tool_name() -> None:
    rule = PermissionRule.parse("Read", RuleAction.ALLOW)
    assert rule.tool_name_glob == "Read"
    assert rule.param_hint is None
    assert rule.action is RuleAction.ALLOW


def test_parse_tool_with_hint() -> None:
    rule = PermissionRule.parse("Bash(npm *)", RuleAction.DENY)
    assert rule.tool_name_glob == "Bash"
    assert rule.param_hint == "npm *"
    assert rule.action is RuleAction.DENY


def test_parse_empty_parens_gives_empty_hint() -> None:
    rule = PermissionRule.parse("Bash()", RuleAction.ALLOW)
    # Parentheses present but empty -> hint is "" (matches empty command only).
    assert rule.param_hint == ""


def test_parse_invalid_rule_raises() -> None:
    with pytest.raises(ValueError):
        PermissionRule.parse("123bad-name!", RuleAction.ALLOW)


# ---------------------------------------------------------------------------
# RuleEngine.check — tool + param matching
# ---------------------------------------------------------------------------


def test_check_returns_ask_when_no_rules() -> None:
    engine = RuleEngine()
    assert engine.check("Read", {}) == "ask"


def test_check_bare_tool_matches_any_params() -> None:
    engine = RuleEngine()
    engine.add_rule(PermissionRule.parse("Read", RuleAction.ALLOW))
    assert engine.check("Read", {"file_path": "/anything"}) == "allow"
    assert engine.check("Write", {"file_path": "/x"}) == "ask"


def test_check_param_hint_glob_matches_primary_param() -> None:
    engine = RuleEngine()
    engine.add_rule(PermissionRule.parse("Bash(npm *)", RuleAction.ALLOW))
    assert engine.check("Bash", {"command": "npm test"}) == "allow"
    assert engine.check("Bash", {"command": "npm run build"}) == "allow"
    # Non-matching command falls through to "ask".
    assert engine.check("Bash", {"command": "rm -rf /"}) == "ask"


def test_check_param_hint_is_full_match_not_prefix() -> None:
    """fnmatch requires a whole-string match, so a bare token is not a prefix."""
    engine = RuleEngine()
    engine.add_rule(PermissionRule.parse("Bash(npm test)", RuleAction.ALLOW))
    assert engine.check("Bash", {"command": "npm test"}) == "allow"
    # "npm test --watch" does NOT match the exact hint "npm test".
    assert engine.check("Bash", {"command": "npm test --watch"}) == "ask"


def test_check_write_path_glob() -> None:
    engine = RuleEngine()
    engine.add_rule(PermissionRule.parse("Write(/tmp/*)", RuleAction.DENY))
    assert engine.check("Write", {"file_path": "/tmp/foo.txt"}) == "deny"
    assert engine.check("Write", {"file_path": "/home/foo.txt"}) == "ask"


def test_parse_rejects_bare_wildcard_tool() -> None:
    """The rule grammar requires an identifier tool name; '*' cannot be parsed."""
    with pytest.raises(ValueError):
        PermissionRule.parse("*", RuleAction.ALLOW)


def test_check_tool_name_glob_via_direct_construction() -> None:
    engine = RuleEngine()
    # A wildcard tool glob is only reachable by constructing the rule directly.
    engine.add_rule(
        PermissionRule(tool_pattern="*", action=RuleAction.ALLOW, tool_name_glob="*")
    )
    assert engine.check("Bash", {"command": "anything"}) == "allow"
    assert engine.check("Read", {}) == "allow"


def test_check_first_match_wins_in_insertion_order() -> None:
    engine = RuleEngine()
    # Deny added first, so it fires before the later allow.
    engine.add_rule(PermissionRule.parse("Bash(rm *)", RuleAction.DENY))
    engine.add_rule(PermissionRule.parse("Bash", RuleAction.ALLOW))
    assert engine.check("Bash", {"command": "rm -rf /"}) == "deny"
    assert engine.check("Bash", {"command": "ls"}) == "allow"


def test_check_params_pattern_multi_key() -> None:
    engine = RuleEngine()
    rule = PermissionRule(
        tool_pattern="Custom",
        action=RuleAction.ALLOW,
        tool_name_glob="Custom",
        params_pattern={"a": "x*", "b": "y*"},
    )
    engine.add_rule(rule)
    assert engine.check("Custom", {"a": "xxx", "b": "yyy"}) == "allow"
    # One key fails to match -> whole rule fails -> "ask".
    assert engine.check("Custom", {"a": "xxx", "b": "zzz"}) == "ask"


# ---------------------------------------------------------------------------
# RuleEngine mutation helpers
# ---------------------------------------------------------------------------


def test_engine_len_clear_and_remove() -> None:
    engine = RuleEngine()
    engine.add_rule(PermissionRule.parse("Read", RuleAction.ALLOW))
    engine.add_rule(PermissionRule.parse("Write", RuleAction.DENY))
    assert len(engine) == 2

    engine.remove_rule(0)
    assert len(engine) == 1
    assert engine.rules[0].tool_name_glob == "Write"

    engine.clear()
    assert len(engine) == 0


def test_remove_rule_out_of_range_raises() -> None:
    engine = RuleEngine()
    with pytest.raises(IndexError):
        engine.remove_rule(0)


def test_rules_property_returns_copy() -> None:
    engine = RuleEngine()
    engine.add_rule(PermissionRule.parse("Read", RuleAction.ALLOW))
    snapshot = engine.rules
    snapshot.clear()
    assert len(engine) == 1  # engine unaffected


# ---------------------------------------------------------------------------
# load_from_settings
# ---------------------------------------------------------------------------


def test_load_from_settings_loads_allow_and_deny() -> None:
    engine = RuleEngine()
    engine.load_from_settings(
        {"permissions": {"allow": ["Read", "Glob"], "deny": ["Bash(rm *)"]}}
    )
    assert len(engine) == 3
    assert engine.check("Read", {}) == "allow"
    assert engine.check("Bash", {"command": "rm -rf /"}) == "deny"


def test_load_from_settings_skips_invalid_rules() -> None:
    engine = RuleEngine()
    engine.load_from_settings(
        {"permissions": {"allow": ["Read", "!!bad!!"], "deny": []}}
    )
    # Only the valid "Read" rule is loaded.
    assert len(engine) == 1


def test_load_from_settings_ignores_non_dict_permissions() -> None:
    engine = RuleEngine()
    engine.load_from_settings({"permissions": "not-a-dict"})
    assert len(engine) == 0


def test_deny_overrides_allow_from_settings() -> None:
    """Deny takes precedence over allow regardless of rule order — a broad
    allow ("Bash") must not shadow a specific deny ("Bash(rm -rf *)")."""
    engine = RuleEngine()
    engine.load_from_settings(
        {"permissions": {"allow": ["Bash"], "deny": ["Bash(rm -rf *)"]}}
    )
    assert engine.check("Bash", {"command": "rm -rf /"}) == "deny"
    # Non-denied Bash commands still hit the broad allow.
    assert engine.check("Bash", {"command": "ls"}) == "allow"


# ---------------------------------------------------------------------------
# modes.py — risk classification
# ---------------------------------------------------------------------------


def test_get_risk_level_known_tools() -> None:
    assert get_risk_level("Read") is ToolRisk.SAFE
    assert get_risk_level("Write") is ToolRisk.CAUTION
    assert get_risk_level("Bash") is ToolRisk.DANGEROUS


def test_get_risk_level_unknown_defaults_dangerous() -> None:
    assert get_risk_level("TotallyUnknownTool") is ToolRisk.DANGEROUS


def test_is_write_tool() -> None:
    assert is_write_tool("Read") is False
    assert is_write_tool("Grep") is False
    assert is_write_tool("Write") is True
    assert is_write_tool("Bash") is True
    assert is_write_tool("Unknown") is True  # defaults dangerous -> write-ish


# ---------------------------------------------------------------------------
# modes.py — needs_permission
# ---------------------------------------------------------------------------


def test_needs_permission_bypass_never_asks() -> None:
    for risk in ToolRisk:
        assert needs_permission(PermissionMode.BYPASS, risk) is False


def test_needs_permission_manual_always_asks() -> None:
    for risk in ToolRisk:
        assert needs_permission(PermissionMode.MANUAL, risk) is True


def test_needs_permission_auto_gates_non_safe() -> None:
    assert needs_permission(PermissionMode.AUTO, ToolRisk.SAFE) is False
    assert needs_permission(PermissionMode.AUTO, ToolRisk.CAUTION) is True
    assert needs_permission(PermissionMode.AUTO, ToolRisk.DANGEROUS) is True


def test_needs_permission_plan_gates_non_safe() -> None:
    assert needs_permission(PermissionMode.PLAN, ToolRisk.SAFE) is False
    assert needs_permission(PermissionMode.PLAN, ToolRisk.CAUTION) is True
    assert needs_permission(PermissionMode.PLAN, ToolRisk.DANGEROUS) is True
