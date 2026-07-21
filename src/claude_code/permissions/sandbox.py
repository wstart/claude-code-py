"""OS-level sandbox enforcement for tool calls.

Provides platform-specific sandbox wrappers that restrict filesystem
access, network access, and resource usage for commands executed by the
``Bash`` tool.

Supported platforms:
- **macOS**: ``sandbox-exec`` with seatbelt (``.sb``) profiles.
- **Linux**: ``bubblewrap`` (``bwrap``) or ``firejail``.
- **Windows**: Basic process-priority restrictions (limited sandbox).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field

from claude_code.utils.logging import get_logger
from claude_code.utils.platform import get_os_info

logger = get_logger("permissions.sandbox")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class SandboxConfig:
    """Configuration for sandbox restrictions.

    Attributes:
        allowed_dirs: Directories the sandboxed process may read/write.
        denied_dirs: Directories explicitly blocked.
        allowed_network_hosts: Hosts allowed for network access.
            Empty list means no network access.
        max_memory_mb: Maximum memory in megabytes (0 = unlimited).
        max_cpu_time_s: Maximum CPU seconds (0 = unlimited).
        allow_write: Whether write access is permitted at all.
    """

    allowed_dirs: list[str] = field(default_factory=list)
    denied_dirs: list[str] = field(default_factory=list)
    allowed_network_hosts: list[str] = field(default_factory=list)
    max_memory_mb: int = 0
    max_cpu_time_s: int = 0
    allow_write: bool = True


# ---------------------------------------------------------------------------
# Availability detection
# ---------------------------------------------------------------------------

def is_sandbox_available() -> bool:
    """Check if any sandbox mechanism is available on the current OS.

    Returns:
        ``True`` if at least one sandbox tool can be used.
    """
    os_info = get_os_info()

    if os_info.is_macos:
        return shutil.which("sandbox-exec") is not None

    if os_info.is_linux:
        return (
            shutil.which("bwrap") is not None
            or shutil.which("firejail") is not None
        )

    # Windows: no real sandbox available through this module.
    return False


def _get_sandbox_backend() -> str | None:
    """Return the name of the best available sandbox backend."""
    os_info = get_os_info()

    if os_info.is_macos and shutil.which("sandbox-exec"):
        return "sandbox-exec"

    if os_info.is_linux:
        if shutil.which("bwrap"):
            return "bwrap"
        if shutil.which("firejail"):
            return "firejail"

    return None


# ---------------------------------------------------------------------------
# macOS seatbelt profile
# ---------------------------------------------------------------------------

def create_seatbelt_profile(config: SandboxConfig) -> str:
    """Generate a macOS sandbox-exec seatbelt profile.

    Args:
        config: Sandbox configuration.

    Returns:
        A ``.sb`` profile string.
    """
    lines: list[str] = []
    lines.append("(version 1)")

    # Default deny everything, then selectively allow.
    lines.append("(deny default)")

    # Always allow basic process operations.
    lines.append("(allow process-exec)")
    lines.append("(allow process-fork)")
    lines.append("(allow signal)")
    lines.append("(allow sysctl-read)")
    lines.append("(allow mach-lookup)")

    # Filesystem access.
    if config.allow_write:
        lines.append("(allow file-read*)")
        for d in config.allowed_dirs:
            lines.append(f'  (allow file-write* (subpath "{d}"))')
        for d in config.denied_dirs:
            lines.append(f'  (deny file-read* (subpath "{d}"))')
            lines.append(f'  (deny file-write* (subpath "{d}"))')
    else:
        lines.append("(deny file-write*)")
        lines.append("(allow file-read*)")
        for d in config.denied_dirs:
            lines.append(f'  (deny file-read* (subpath "{d}"))')

    # Network access.
    if config.allowed_network_hosts:
        lines.append("(deny network*)")
        for host in config.allowed_network_hosts:
            lines.append(f'  (allow network-outbound (regex #"{host}"))')
    else:
        lines.append("(deny network*)")

    # Resource limits (seatbelt doesn't natively support CPU/memory
    # limits — those are handled via ulimit or setrlimit in the wrapper).
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Linux bubblewrap arguments
# ---------------------------------------------------------------------------

def create_bwrap_args(config: SandboxConfig) -> list[str]:
    """Generate bubblewrap command-line arguments.

    Args:
        config: Sandbox configuration.

    Returns:
        List of arguments to prepend before the actual command.
    """
    args: list[str] = ["bwrap"]

    # Base filesystem: bind-mount essential system dirs read-only.
    for sys_dir in ["/usr", "/bin", "/lib", "/lib64", "/etc", "/dev"]:
        args.extend(["--ro-bind", sys_dir, sys_dir])

    # Bind-mount /proc (needed by many programs).
    args.extend(["--proc", "/proc"])

    # Allowed directories.
    for d in config.allowed_dirs:
        if config.allow_write:
            args.extend(["--bind", d, d])
        else:
            args.extend(["--ro-bind", d, d])

    # Denied directories — bind /dev/null over them.
    for d in config.denied_dirs:
        args.extend(["--bind", "/dev/null", d])

    # Tmpfs for /tmp.
    args.extend(["--tmpfs", "/tmp"])

    # Network.
    if not config.allowed_network_hosts:
        args.append("--unshare-net")

    # New PID namespace (isolate process listing).
    args.append("--unshare-pid")

    # Resource limits via rlimit.
    if config.max_memory_mb > 0:
        max_bytes = config.max_memory_mb * 1024 * 1024
        args.extend(["--rlimit", "as", str(max_bytes), str(max_bytes)])

    if config.max_cpu_time_s > 0:
        args.extend([
            "--rlimit", "cpu",
            str(config.max_cpu_time_s),
            str(config.max_cpu_time_s),
        ])

    # Working directory.
    args.extend(["--chdir", "/"])

    return args


# ---------------------------------------------------------------------------
# SandboxRunner
# ---------------------------------------------------------------------------

class SandboxRunner:
    """Wraps shell commands with OS-level sandbox restrictions.

    Example::

        runner = SandboxRunner()
        if runner.available:
            wrapped = runner.wrap_command("npm test", config)
            # wrapped might be: ["sandbox-exec", "-f", "/tmp/profile.sb",
            #                    "/bin/sh", "-c", "npm test"]
    """

    def __init__(self, config: SandboxConfig | None = None) -> None:
        self.config = config or SandboxConfig()
        self._backend = _get_sandbox_backend()

    @property
    def available(self) -> bool:
        """Whether a sandbox backend is available."""
        return self._backend is not None

    @property
    def backend(self) -> str | None:
        """Name of the detected sandbox backend, or ``None``."""
        return self._backend

    def wrap_command(
        self,
        cmd: str,
        config: SandboxConfig | None = None,
    ) -> list[str]:
        """Wrap a shell command with sandbox restrictions.

        If no sandbox backend is available, the command is returned
        unmodified (as ``["/bin/sh", "-c", cmd]``).

        Args:
            cmd: The shell command string.
            config: Override config for this call.  Falls back to the
                instance-level config.

        Returns:
            A list of command tokens suitable for
            ``asyncio.create_subprocess_exec``.
        """
        cfg = config or self.config

        if self._backend == "sandbox-exec":
            return self._wrap_macos(cmd, cfg)

        if self._backend == "bwrap":
            return self._wrap_bwrap(cmd, cfg)

        if self._backend == "firejail":
            return self._wrap_firejail(cmd, cfg)

        # No sandbox available — return plain command.
        logger.debug("No sandbox backend; running command unwrapped")
        return ["/bin/sh", "-c", cmd]

    # -- platform wrappers -------------------------------------------------

    def _wrap_macos(self, cmd: str, config: SandboxConfig) -> list[str]:
        """Wrap with macOS sandbox-exec."""
        import os
        import tempfile

        profile = create_seatbelt_profile(config)

        # Write profile to a temporary file.
        fd, profile_path = tempfile.mkstemp(suffix=".sb", prefix="claude_sb_")
        try:
            os.write(fd, profile.encode("utf-8"))
        finally:
            os.close(fd)

        shell = "bash"
        cmd_parts = [
            "sandbox-exec", "-f", profile_path,
            shell, "-c", cmd,
        ]

        logger.debug(
            "macOS sandbox: %s",
            " ".join(cmd_parts),
        )
        return cmd_parts

    def _wrap_bwrap(self, cmd: str, config: SandboxConfig) -> list[str]:
        """Wrap with Linux bubblewrap."""
        bwrap_args = create_bwrap_args(config)
        shell = "bash"
        full = bwrap_args + [shell, "-c", cmd]

        logger.debug("bwrap sandbox: %s", " ".join(full))
        return full

    def _wrap_firejail(self, cmd: str, config: SandboxConfig) -> list[str]:
        """Wrap with firejail (Linux fallback)."""
        args = ["firejail", "--quiet"]

        if not config.allow_write:
            args.append("--read-only=/")

        if not config.allowed_network_hosts:
            args.append("--net=none")

        if config.max_memory_mb > 0:
            # firejail doesn't have a direct memory limit flag;
            # use rlimit-as via a profile line.
            max_bytes = config.max_memory_mb * 1024 * 1024
            args.append(f"--rlimit-as={max_bytes}")

        for d in config.allowed_dirs:
            if config.allow_write:
                args.append(f"--whitelist={d}")

        args.extend(["/bin/sh", "-c", cmd])

        logger.debug("firejail sandbox: %s", " ".join(args))
        return args
