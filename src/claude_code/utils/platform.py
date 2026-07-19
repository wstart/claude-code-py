"""Platform detection utilities."""

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class OSInfo:
    """Operating system information."""

    name: str  # e.g. "macOS", "Linux", "Windows"
    version: str
    arch: str  # e.g. "arm64", "x86_64"
    is_macos: bool
    is_linux: bool
    is_windows: bool


def get_os_info() -> OSInfo:
    """Detect the current operating system.

    Returns:
        OSInfo with platform details.
    """
    system = platform.system()
    is_macos = system == "Darwin"
    is_linux = system == "Linux"
    is_windows = system == "Windows"

    if is_macos:
        name = "macOS"
        try:
            version = platform.mac_ver()[0]
        except Exception:
            version = platform.release()
    elif is_linux:
        name = "Linux"
        version = platform.release()
    elif is_windows:
        name = "Windows"
        version = platform.version()
    else:
        name = system
        version = platform.release()

    return OSInfo(
        name=name,
        version=version,
        arch=platform.machine(),
        is_macos=is_macos,
        is_linux=is_linux,
        is_windows=is_windows,
    )


def get_shell() -> str:
    """Get the user's default shell.

    Returns:
        Shell path (e.g. "/bin/zsh") or "bash" as fallback.
    """
    shell = os.environ.get("SHELL", "")
    if shell:
        return shell

    if sys.platform == "win32":
        # Check common Windows shells
        for candidate in ["pwsh.exe", "powershell.exe", "cmd.exe"]:
            path = shutil.which(candidate)
            if path:
                return path
        return "cmd.exe"

    return "/bin/bash"


def has_command(command: str) -> bool:
    """Check if a command is available on the system PATH.

    Args:
        command: The command name to look up.

    Returns:
        True if the command exists, False otherwise.
    """
    return shutil.which(command) is not None


@dataclass(frozen=True)
class TerminalSize:
    """Terminal dimensions."""

    columns: int
    rows: int


def get_terminal_size() -> TerminalSize:
    """Get the current terminal size.

    Returns:
        TerminalSize with columns and rows. Falls back to 80x24
        if the terminal size cannot be determined.
    """
    try:
        size = shutil.get_terminal_size(fallback=(80, 24))
        return TerminalSize(columns=size.columns, rows=size.lines)
    except Exception:
        return TerminalSize(columns=80, rows=24)


def is_tty() -> bool:
    """Check if stdout is connected to a TTY.

    Returns:
        True if stdout is a terminal, False otherwise (e.g. piped).
    """
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def get_env_or_default(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get an environment variable with a default value.

    Args:
        key: Environment variable name.
        default: Default value if not set.

    Returns:
        The environment variable value or the default.
    """
    return os.environ.get(key, default)
