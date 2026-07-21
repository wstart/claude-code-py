"""Skill manager — discover, load, and access skills.

Skills are specialized instruction sets that extend Claude's
capabilities with domain-specific knowledge and workflows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from claude_code.skills.discovery import SkillDiscovery
from claude_code.utils.logging import get_logger

logger = get_logger("skills.manager")


@dataclass
class Skill:
    """A loaded skill with its metadata and instructions.

    Attributes:
        name: Unique skill identifier.
        description: Human-readable description.
        instructions: The skill's prompt / instruction content.
        source: Where the skill was loaded from (path or ``builtin``).
        triggers: Keywords that should auto-activate this skill.
        tags: Categorization tags.
    """

    name: str
    description: str = ""
    instructions: str = ""
    source: str = ""
    triggers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize skill metadata to a dict."""
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "triggers": self.triggers,
            "tags": self.tags,
        }


class SkillManager:
    """Manages skills (specialized instruction sets).

    Discovers skills from configured directories and built-in
    locations, caches them for quick lookup.
    """

    def __init__(self, skill_dirs: list[str] | None = None) -> None:
        """Initialize with optional additional skill directories.

        Args:
            skill_dirs: Extra directories to scan for skills.
        """
        self._discovery = SkillDiscovery(extra_dirs=skill_dirs)
        self._cache: dict[str, Skill] = {}
        self._discovered = False

    def discover(self) -> list[Skill]:
        """Find all available skills.

        Results are cached after the first call.

        Returns:
            List of discovered :class:`Skill` instances.
        """
        if self._discovered:
            return list(self._cache.values())

        skills = self._discovery.scan_all()
        for skill in skills:
            self._cache[skill.name] = skill

        self._discovered = True
        logger.info("Discovered %d skills", len(self._cache))
        return list(self._cache.values())

    def load(self, name: str) -> Skill:
        """Load a skill's full instructions by name.

        Args:
            name: Skill name.

        Returns:
            The :class:`Skill` with instructions populated.

        Raises:
            KeyError: If no skill with *name* exists.
        """
        # Ensure discovery has run
        if not self._discovered:
            self.discover()

        skill = self._cache.get(name)
        if skill is None:
            raise KeyError(f"Skill '{name}' not found")

        # Load full instructions if not already loaded
        if not skill.instructions:
            loaded = self._discovery.load_instructions(skill)
            if loaded:
                skill.instructions = loaded

        return skill

    def get_skill(self, name: str) -> Skill | None:
        """Look up a skill by name without raising.

        Args:
            name: Skill name.

        Returns:
            The :class:`Skill` if found, ``None`` otherwise.
        """
        try:
            return self.load(name)
        except KeyError:
            return None

    def list_skills(self) -> list[dict[str, Any]]:
        """List all available skills with metadata.

        Returns:
            List of skill info dicts.
        """
        if not self._discovered:
            self.discover()

        return [skill.to_dict() for skill in self._cache.values()]

    def find_by_trigger(self, keyword: str) -> list[Skill]:
        """Find skills whose triggers match a keyword.

        Args:
            keyword: Text to search for in skill triggers.

        Returns:
            Matching skills.
        """
        if not self._discovered:
            self.discover()

        keyword_lower = keyword.lower()
        return [
            skill for skill in self._cache.values()
            if any(keyword_lower in t.lower() for t in skill.triggers)
        ]
