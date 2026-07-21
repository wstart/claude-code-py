"""Claude Code Py - Python implementation of Claude Code CLI."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the installed distribution's version.
    __version__ = _pkg_version("aka_claude")
except PackageNotFoundError:  # not installed (e.g. running from a raw checkout)
    __version__ = "0.0.0+unknown"

__app_name__ = "aka"
