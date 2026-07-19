"""Git worktree isolation for concurrent agent work.

Public API
----------
- :class:`WorktreeManager` — create, list, and clean up git worktrees.
- :class:`WorktreeError` — raised on worktree operation failures.
"""

from claude_code.worktree.manager import WorktreeError, WorktreeManager

__all__ = [
    "WorktreeError",
    "WorktreeManager",
]
