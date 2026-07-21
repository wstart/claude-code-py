"""Path resolution and access control utilities."""

import os
from pathlib import Path

from claude_code.utils.git_utils import get_repo_root


def resolve_path(path: str | Path, working_dir: str | Path | None = None) -> Path:
    """Resolve a path relative to a working directory.

    If the path is absolute, it's returned as-is. If relative, it's resolved
    against working_dir (or cwd if not provided).

    Args:
        path: The path to resolve.
        working_dir: Base directory for relative paths.

    Returns:
        An absolute, resolved Path.
    """
    p = Path(path)
    if p.is_absolute():
        return p.resolve()

    base = Path(working_dir) if working_dir else Path.cwd()
    return (base / p).resolve()


def is_path_allowed(
    path: str | Path,
    allowed_dirs: list[str | Path] | None = None,
) -> bool:
    """Check whether a path falls within one of the allowed directories.

    If allowed_dirs is None or empty, all paths are allowed.

    Args:
        path: The path to check.
        allowed_dirs: List of directories the path must be under.

    Returns:
        True if the path is accessible, False otherwise.
    """
    if not allowed_dirs:
        return True

    resolved = Path(path).resolve()
    for allowed in allowed_dirs:
        allowed_resolved = Path(allowed).resolve()
        try:
            resolved.relative_to(allowed_resolved)
            return True
        except ValueError:
            continue
    return False


def get_project_root(working_dir: str | Path | None = None) -> Path:
    """Find the project root directory.

    Tries (in order):
    1. Git repository root
    2. The working directory itself
    3. Current working directory

    Args:
        working_dir: Starting directory for the search.

    Returns:
        The project root as an absolute Path.
    """
    start = Path(working_dir) if working_dir else Path.cwd()

    git_root = get_repo_root(str(start))
    if git_root:
        return Path(git_root)

    return start.resolve()


def find_upward(
    filename: str,
    start_dir: str | Path | None = None,
    stop_at: str | Path | None = None,
) -> Path | None:
    """Search for a file by walking upward from start_dir.

    Args:
        filename: Name of the file to find.
        start_dir: Directory to start searching from.
        stop_at: Directory to stop searching at (exclusive).

    Returns:
        Path to the file if found, None otherwise.
    """
    current = Path(start_dir or Path.cwd()).resolve()
    stop = Path(stop_at).resolve() if stop_at else None

    while True:
        candidate = current / filename
        if candidate.exists():
            return candidate

        parent = current.parent
        if parent == current:
            break
        if stop and current == stop:
            break
        current = parent

    return None


def safe_relative(path: str | Path, base: str | Path) -> str:
    """Get a relative path string, falling back to absolute if not possible.

    Args:
        path: Target path.
        base: Base path to compute relative to.

    Returns:
        Relative path string, or absolute if relative computation fails.
    """
    try:
        return str(Path(path).resolve().relative_to(Path(base).resolve()))
    except ValueError:
        return str(Path(path).resolve())


def ensure_dir(path: str | Path) -> Path:
    """Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path to create.

    Returns:
        The resolved Path.
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p.resolve()


def expand_user(path: str | Path) -> Path:
    """Expand ~ in a path to the user's home directory.

    Args:
        path: Path potentially containing ~.

    Returns:
        Expanded Path.
    """
    return Path(os.path.expanduser(str(path)))
