"""Git repository utilities using subprocess."""

import subprocess


def _run_git(*args: str, cwd: str | None = None) -> str | None:
    """Run a git command and return stdout, or None on failure.

    Args:
        *args: Git subcommand and arguments.
        cwd: Working directory for the command.

    Returns:
        Stripped stdout string, or None if git is not available or command fails.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def is_git_repo(path: str | None = None) -> bool:
    """Check if the given path is inside a git repository.

    Args:
        path: Directory to check. Defaults to cwd.

    Returns:
        True if inside a git repo, False otherwise.
    """
    result = _run_git("rev-parse", "--is-inside-work-tree", cwd=path)
    return result == "true"


def get_repo_root(path: str | None = None) -> str | None:
    """Get the root directory of the git repository.

    Args:
        path: A path inside the repo. Defaults to cwd.

    Returns:
        Absolute path to the repo root, or None if not in a repo.
    """
    return _run_git("rev-parse", "--show-toplevel", cwd=path)


def get_current_branch(path: str | None = None) -> str | None:
    """Get the name of the current branch.

    Args:
        path: A path inside the repo.

    Returns:
        Branch name string, or None if not in a repo or in detached HEAD.
    """
    return _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)


def get_git_status(path: str | None = None, porcelain: bool = True) -> str | None:
    """Get the output of `git status`.

    Args:
        path: Repo path. Defaults to cwd.
        porcelain: Use machine-readable output format.

    Returns:
        Status output string, or None on failure.
    """
    args = ["status"]
    if porcelain:
        args.append("--porcelain")
    return _run_git(*args, cwd=path)


def get_git_diff(
    path: str | None = None,
    staged: bool = False,
    stat: bool = False,
) -> str | None:
    """Get the output of `git diff`.

    Args:
        path: Repo path.
        staged: Show staged changes (--cached).
        stat: Show diffstat summary instead of full diff.

    Returns:
        Diff output string, or None on failure.
    """
    args = ["diff"]
    if staged:
        args.append("--cached")
    if stat:
        args.append("--stat")
    return _run_git(*args, cwd=path)


def get_git_log(
    path: str | None = None,
    max_count: int = 10,
    oneline: bool = True,
) -> str | None:
    """Get the output of `git log`.

    Args:
        path: Repo path.
        max_count: Maximum number of commits to show.
        oneline: Use compact one-line format.

    Returns:
        Log output string, or None on failure.
    """
    args = ["log", f"--max-count={max_count}"]
    if oneline:
        args.append("--oneline")
    return _run_git(*args, cwd=path)


def get_git_remote_url(path: str | None = None, remote: str = "origin") -> str | None:
    """Get the URL of a git remote.

    Args:
        path: Repo path.
        remote: Remote name (default "origin").

    Returns:
        Remote URL string, or None if remote doesn't exist.
    """
    return _run_git("remote", "get-url", remote, cwd=path)


def get_head_sha(path: str | None = None, short: bool = True) -> str | None:
    """Get the SHA of the current HEAD commit.

    Args:
        path: Repo path.
        short: Return abbreviated SHA.

    Returns:
        Commit SHA string, or None on failure.
    """
    args = ["rev-parse"]
    if short:
        args.append("--short")
    args.append("HEAD")
    return _run_git(*args, cwd=path)
