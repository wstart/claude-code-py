"""Skills system — specialized instruction sets and slash commands."""

from claude_code.skills.discovery import SkillDiscovery
from claude_code.skills.manager import Skill, SkillManager

__all__ = [
    "Skill",
    "SkillDiscovery",
    "SkillManager",
]
