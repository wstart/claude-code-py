"""Skill discovery engine — locate skills from filesystem and built-ins.

Scans ``.claude/skills/`` directories, project-local skill folders,
and built-in skill definitions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from claude_code.utils.logging import get_logger
from claude_code.utils.path_utils import expand_user, get_project_root

logger = get_logger("skills.discovery")

# Standard directories to search for skills
_SKILL_SEARCH_PATHS: list[str] = [
    "~/.claude/skills/",
]

# Built-in skills shipped with the package
_BUILTIN_SKILLS_DIR = Path(__file__).parent / "builtin"


class SkillDiscovery:
    """Discover skills from ``.claude/skills/`` directories and built-ins.

    Skills are directories containing either:
    - A ``skill.json`` manifest + ``instructions.md`` file
    - A single ``<name>.md`` file with YAML front-matter
    """

    def __init__(self, extra_dirs: list[str] | None = None) -> None:
        """Initialize with optional extra directories to scan.

        Args:
            extra_dirs: Additional directories to search for skills.
        """
        self._extra_dirs = extra_dirs or []

    def scan_all(self) -> list["Skill"]:
        """Scan all configured and extra directories for skills.

        Returns:
            Deduplicated list of discovered skills (by name).
        """
        from claude_code.skills.manager import Skill

        seen: dict[str, Skill] = {}

        # Built-in skills
        for skill in self.scan_builtin_skills():
            seen[skill.name] = skill

        # Standard search paths
        for path_str in _SKILL_SEARCH_PATHS:
            path = expand_user(path_str)
            for skill in self.scan_directory(str(path)):
                seen[skill.name] = skill

        # Project-level skills
        project_root = get_project_root()
        project_skills = project_root / ".claude" / "skills"
        for skill in self.scan_directory(str(project_skills)):
            seen[skill.name] = skill

        # Extra directories
        for dir_str in self._extra_dirs:
            for skill in self.scan_directory(dir_str):
                seen[skill.name] = skill

        return list(seen.values())

    def scan_directory(self, path: str) -> list["Skill"]:
        """Scan a single directory for skill definitions.

        Args:
            path: Directory path to scan.

        Returns:
            List of skills found in the directory.
        """
        from claude_code.skills.manager import Skill

        directory = Path(path)
        if not directory.is_dir():
            return []

        skills: list[Skill] = []

        for child in directory.iterdir():
            if child.is_dir():
                # Directory-based skill: skill.json + instructions.md
                manifest_file = child / "skill.json"
                if manifest_file.exists():
                    skill = self._load_directory_skill(child, manifest_file)
                    if skill:
                        skills.append(skill)
            elif child.suffix == ".md" and child.is_file():
                # Single-file skill with optional YAML front-matter
                skill = self._load_file_skill(child)
                if skill:
                    skills.append(skill)

        return skills

    def scan_builtin_skills(self) -> list["Skill"]:
        """Scan the built-in skills directory.

        Returns:
            List of built-in skills.
        """
        if not _BUILTIN_SKILLS_DIR.is_dir():
            return []
        return self.scan_directory(str(_BUILTIN_SKILLS_DIR))

    def find_skill(self, name: str) -> Optional["Skill"]:
        """Search all locations for a specific skill by name.

        Args:
            name: Skill name to find.

        Returns:
            The :class:`Skill` if found, ``None`` otherwise.
        """
        for skill in self.scan_all():
            if skill.name == name:
                return skill
        return None

    def load_instructions(self, skill: "Skill") -> str:
        """Load the full instruction text for a skill.

        Args:
            skill: The skill to load instructions for.

        Returns:
            The instruction text, or empty string if not loadable.
        """
        source = Path(skill.source)

        if source.is_dir():
            instructions_file = source / "instructions.md"
            if instructions_file.exists():
                try:
                    return instructions_file.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    logger.warning("Failed to read %s: %s", instructions_file, exc)
        elif source.is_file():
            try:
                content = source.read_text(encoding="utf-8").strip()
                # Strip YAML front-matter if present
                return _strip_frontmatter(content)
            except OSError as exc:
                logger.warning("Failed to read %s: %s", source, exc)

        return ""

    # -- internals ---------------------------------------------------------

    def _load_directory_skill(
        self, directory: Path, manifest_file: Path,
    ) -> Optional["Skill"]:
        """Load a skill from a directory with skill.json."""
        from claude_code.skills.manager import Skill

        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Invalid skill.json at %s: %s", manifest_file, exc)
            return None

        name = data.get("name", directory.name)
        return Skill(
            name=name,
            description=data.get("description", ""),
            source=str(directory),
            triggers=data.get("triggers", []),
            tags=data.get("tags", []),
        )

    def _load_file_skill(self, filepath: Path) -> Optional["Skill"]:
        """Load a skill from a single .md file."""
        from claude_code.skills.manager import Skill

        name = filepath.stem
        description = ""

        try:
            content = filepath.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(content)
            if frontmatter:
                name = frontmatter.get("name", name)
                description = frontmatter.get("description", "")
        except OSError as exc:
            logger.warning("Failed to read skill file %s: %s", filepath, exc)
            return None

        return Skill(
            name=name,
            description=description,
            source=str(filepath),
        )


def _parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML-like front-matter from markdown content.

    Supports simple ``key: value`` pairs between ``---`` fences.

    Args:
        content: Raw markdown content.

    Returns:
        Dict of front-matter key-value pairs, or empty dict.
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}

    result: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            result[key.strip()] = value.strip()

    return result


def _strip_frontmatter(content: str) -> str:
    """Remove YAML front-matter from markdown content.

    Args:
        content: Raw markdown with optional front-matter.

    Returns:
        Content without the front-matter block.
    """
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return content

    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:]).strip()

    return content
