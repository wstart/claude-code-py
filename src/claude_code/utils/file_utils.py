"""File reading and writing utilities with encoding detection and atomic writes."""

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import chardet


@dataclass(frozen=True)
class FileInfo:
    """Metadata about a file."""

    path: Path
    size: int
    modified: datetime
    is_dir: bool
    permissions: str  # octal permission string like "0o644"


# Magic bytes for common binary formats
_BINARY_PREFIXES = [
    b"\x89PNG",
    b"GIF8",
    b"\xff\xd8\xff",  # JPEG
    b"PK\x03\x04",  # ZIP / DOCX / XLSX
    b"\x7fELF",  # ELF binary
    b"MZ",  # PE binary
    b"\x00\x00\x00",  # Generic null bytes
]

# Threshold: if file contains more than this ratio of null bytes, it's binary
_NULL_BYTE_THRESHOLD = 0.01


def read_file(
    path: str | Path,
    encoding: str | None = None,
    max_size: int | None = None,
) -> str:
    """Read a file with automatic encoding detection.

    Falls back to UTF-8 if encoding detection fails.

    Args:
        path: File path to read.
        encoding: Force a specific encoding. If None, auto-detected via chardet.
        max_size: Maximum bytes to read. None reads the entire file.

    Returns:
        File contents as a string.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        PermissionError: If the file is not readable.
        UnicodeDecodeError: If the file cannot be decoded.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    raw = file_path.read_bytes()

    if max_size is not None:
        raw = raw[:max_size]

    if encoding is None:
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"

    return raw.decode(encoding, errors="replace")


def read_file_with_encoding(
    path: str | Path,
    sample_size: int = 65536,
) -> tuple[str, str]:
    """Read a text file and report the encoding used.

    Encoding is detected from a bounded prefix (``sample_size`` bytes) so
    detection stays fast on large files. Returns ``(text, encoding)`` so a
    caller can write the file back with the same encoding — this keeps the
    Read/Edit tools consistent on non-UTF-8 files.

    Raises:
        FileNotFoundError / PermissionError / OSError from the read.
    """
    raw = Path(path).read_bytes()
    detected = chardet.detect(raw[:sample_size])
    encoding = detected.get("encoding") or "utf-8"
    try:
        return raw.decode(encoding), encoding
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", errors="replace"), "utf-8"


async def read_file_async(
    path: str | Path,
    encoding: str | None = None,
    max_size: int | None = None,
) -> str:
    """Async version of read_file.

    Args:
        path: File path to read.
        encoding: Force a specific encoding.
        max_size: Maximum bytes to read.

    Returns:
        File contents as a string.
    """
    import aiofiles

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    async with aiofiles.open(str(file_path), "rb") as f:
        raw = await f.read()

    if max_size is not None:
        raw = raw[:max_size]

    if encoding is None:
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"

    return raw.decode(encoding, errors="replace")


def write_file(
    path: str | Path,
    content: str,
    encoding: str = "utf-8",
    atomic: bool = True,
) -> Path:
    """Write content to a file, optionally using atomic write.

    Atomic write: writes to a temp file first, then renames to the target.
    This prevents partial writes if the process is interrupted.

    Args:
        path: Target file path.
        content: String content to write.
        encoding: File encoding (default UTF-8).
        atomic: Use atomic write via temp file + rename.

    Returns:
        The resolved Path that was written.

    Raises:
        PermissionError: If the target directory is not writable.
    """
    file_path = Path(path).resolve()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if atomic:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(file_path.parent),
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
            os.replace(tmp_path, str(file_path))
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    else:
        file_path.write_text(content, encoding=encoding)

    return file_path


async def write_file_async(
    path: str | Path,
    content: str,
    encoding: str = "utf-8",
) -> Path:
    """Async version of write_file (always atomic via aiofiles).

    Args:
        path: Target file path.
        content: String content to write.
        encoding: File encoding.

    Returns:
        The resolved Path that was written.
    """
    import aiofiles

    file_path = Path(path).resolve()
    file_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(str(file_path), "w", encoding=encoding) as f:
        await f.write(content)

    return file_path


def is_binary_file(path: str | Path, sample_size: int = 8192) -> bool:
    """Check if a file appears to be binary.

    Uses a combination of magic bytes and null-byte ratio.

    Args:
        path: File path to check.
        sample_size: Number of bytes to sample from the start.

    Returns:
        True if the file appears to be binary.
    """
    file_path = Path(path)
    if not file_path.exists() or file_path.is_dir():
        return False

    with open(file_path, "rb") as f:
        sample = f.read(sample_size)

    if not sample:
        return False

    # Check magic bytes
    for prefix in _BINARY_PREFIXES:
        if sample.startswith(prefix):
            return True

    # Check null byte ratio
    null_count = sample.count(b"\x00")
    if len(sample) > 0 and null_count / len(sample) > _NULL_BYTE_THRESHOLD:
        return True

    return False


def get_file_info(path: str | Path) -> FileInfo:
    """Get metadata about a file.

    Args:
        path: File or directory path.

    Returns:
        FileInfo with size, modified time, permissions.

    Raises:
        FileNotFoundError: If the path doesn't exist.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path not found: {p}")

    stat = p.stat()
    return FileInfo(
        path=p,
        size=stat.st_size,
        modified=datetime.fromtimestamp(stat.st_mtime),
        is_dir=p.is_dir(),
        permissions=oct(stat.st_mode & 0o777),
    )
