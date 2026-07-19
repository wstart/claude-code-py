"""Git worktree isolation for agent work.

The :class:`WorktreeManager` creates and manages git worktrees so that
agents can work in isolated directory copies without interfering with
each other or the main working tree.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Prefix for auto-generated worktree directories
_WORKTREE_PREFIX = ".claude/worktrees/"


class WorktreeError(Exception):
    """Raised on worktree operation failures."""


class WorktreeManager:
    """Manages git worktrees for isolated agent work.

    Creates worktrees under ``<repo_root>/.claude/worktrees/`` and
    provides cleanup utilities to remove them when done.

    Parameters
    ----------
    repo_root:
        Absolute path to the git repository root.
    """

    def __init__(self, repo_root: str) -> None:
        self._repo_root = str(Path(repo_root).resolve())
        self._worktree_base = os.path.join(self._repo_root, _WORKTREE_PREFIX)
        self._created: list[str] = []

    async def create(self, name: str, base_ref: str = "HEAD") -> str:
        """Create a new worktree and return its absolute path.

        The worktree is created at
        ``<repo_root>/.claude/worktrees/<name>`` on a new branch
        derived from *base_ref*.

        Args:
            name: Short name for the worktree (used as branch and dir name).
            base_ref: Git ref to base the new branch on (default HEAD).

        Returns:
            Absolute path to the new worktree directory.

        Raises:
            WorktreeError: If git operations fail.
        """
        worktree_path = os.path.join(self._worktree_base, name)
        branch_name = f"claude/worktree/{name}"

        # Ensure the base directory exists
        os.makedirs(self._worktree_base, exist_ok=True)

        # Check if worktree already exists
        if os.path.isdir(worktree_path):
            logger.info("Worktree '%s' already exists at %s", name, worktree_path)
            return worktree_path

        # Create the worktree with a new branch
        returncode, stdout, stderr = await self._run_git(
            "worktree", "add", "-b", branch_name, worktree_path, base_ref,
        )

        if returncode != 0:
            # If branch already exists, try without -b
            if "already exists" in stderr:
                returncode, stdout, stderr = await self._run_git(
                    "worktree", "add", worktree_path, branch_name,
                )

            if returncode != 0:
                raise WorktreeError(
                    f"Failed to create worktree '{name}': {stderr.strip()}"
                )

        self._created.append(worktree_path)
        logger.info("Created worktree '%s' at %s", name, worktree_path)
        return worktree_path

    async def remove(self, path: str, discard_changes: bool = False) -> None:
        """Remove a worktree.

        Args:
            path: Absolute path to the worktree directory.
            discard_changes: If True, discard uncommitted changes.

        Raises:
            WorktreeError: If removal fails and discard_changes is False.
        """
        resolved = str(Path(path).resolve())

        if discard_changes:
            # Force remove: discard any uncommitted changes
            returncode, _, stderr = await self._run_git(
                "worktree", "remove", "--force", resolved,
            )
        else:
            returncode, _, stderr = await self._run_git(
                "worktree", "remove", resolved,
            )

        if returncode != 0:
            raise WorktreeError(
                f"Failed to remove worktree at '{resolved}': {stderr.strip()}"
            )

        if resolved in self._created:
            self._created.remove(resolved)

        logger.info("Removed worktree at %s", resolved)

    async def list(self) -> list[dict[str, Any]]:
        """List all worktrees in the repository.

        Returns:
            List of dicts with ``path``, ``head``, and ``branch`` keys.
        """
        returncode, stdout, _ = await self._run_git(
            "worktree", "list", "--porcelain",
        )

        if returncode != 0:
            return []

        worktrees: list[dict[str, Any]] = []
        current: dict[str, Any] = {}

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
                continue

            if line.startswith("worktree "):
                current["path"] = line[len("worktree "):]
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):]
            elif line.startswith("branch "):
                current["branch"] = line[len("branch "):]
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True

        if current:
            worktrees.append(current)

        return worktrees

    async def cleanup(self) -> None:
        """Remove all temporary worktrees created by this manager.

        Worktrees are removed from disk and pruned from git's records.
        Errors during individual removals are logged but do not stop
        cleanup of remaining worktrees.
        """
        for path in list(self._created):
            try:
                await self.remove(path, discard_changes=True)
            except WorktreeError:
                logger.warning(
                    "Failed to remove worktree '%s', trying manual cleanup",
                    path,
                )
                # Fallback: remove directory manually
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)

        # Prune stale worktree records
        await self._run_git("worktree", "prune")
        self._created.clear()
        logger.info("Cleaned up all temporary worktrees")

    @property
    def repo_root(self) -> str:
        """The repository root path."""
        return self._repo_root

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    async def _run_git(self, *args: str) -> tuple[int, str, str]:
        """Run a git command asynchronously.

        Returns:
            Tuple of (return_code, stdout, stderr).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._repo_root,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=30,
            )
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return proc.returncode or 0, stdout, stderr
        except asyncio.TimeoutError:
            return -1, "", "Git command timed out"
        except FileNotFoundError:
            return -1, "", "git executable not found"
