"""Read tool — read file contents with line numbers, images, and PDFs.

Mirrors the ``cat -n`` style output used by Claude Code's TypeScript
implementation.  Binary image files are returned as base64 content blocks
suitable for the multimodal API.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult

# Maximum line length before truncation
_MAX_LINE_LENGTH = 2000

# Default maximum number of lines to return
_DEFAULT_LIMIT = 2000

# Reject text files larger than this to avoid loading GBs into memory.
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

# Only sample this many leading bytes for encoding detection — running
# chardet over a whole large file is very slow.
_CHARDET_SAMPLE = 64 * 1024

# File extensions we treat as images (return base64)
_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".ico",
})

# MIME type mapping for image content blocks
_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".ico": "image/x-icon",
}


class ReadTool(Tool):
    """Read a file from disk and return its contents.

    Text files are returned with line numbers (``cat -n`` format).
    Image files are returned as base64-encoded content blocks.
    """

    name = "Read"
    description = (
        "Reads a file from the local filesystem. Returns the file contents "
        "with line numbers for text files, or base64-encoded data for images. "
        "Supports offset/limit for reading specific line ranges."
    )
    category = "file"
    read_only = True
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-based).",
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return.",
                "minimum": 1,
                "maximum": 10000,
            },
        },
        "required": ["file_path"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        file_path_str: str = kwargs["file_path"]
        offset: int = kwargs.get("offset", 1)
        limit: int = kwargs.get("limit", _DEFAULT_LIMIT)

        path = Path(file_path_str).expanduser().resolve()

        if not path.exists():
            return ToolResult.error(f"File not found: {path}")

        if not path.is_file():
            return ToolResult.error(f"Not a file: {path}")

        # -- Image files ---------------------------------------------------
        if path.suffix.lower() in _IMAGE_EXTENSIONS:
            return await self._read_image(path)

        # -- PDF files -----------------------------------------------------
        if path.suffix.lower() == ".pdf":
            return await self._read_pdf(path)

        # -- Text files ----------------------------------------------------
        return await self._read_text(path, offset, limit)

    # -- private helpers ---------------------------------------------------

    async def _read_text(self, path: Path, offset: int, limit: int) -> ToolResult:
        """Read a text file with encoding detection and line numbers."""
        import chardet

        try:
            file_size = path.stat().st_size
        except OSError as exc:
            return ToolResult.error(f"Cannot stat file: {exc}")

        if file_size > _MAX_FILE_SIZE:
            return ToolResult.error(
                f"File too large to read: {file_size} bytes "
                f"(limit {_MAX_FILE_SIZE}). Use a shell tool (e.g. sed/head) "
                "to extract the range you need."
            )

        try:
            raw_bytes = path.read_bytes()
        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except OSError as exc:
            return ToolResult.error(f"Cannot read file: {exc}")

        # Detect encoding from a bounded prefix (chardet over a whole file
        # is slow); the detected encoding is reused for the full decode.
        detected = chardet.detect(raw_bytes[:_CHARDET_SAMPLE])
        encoding = detected.get("encoding") or "utf-8"

        try:
            text = raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            # Fallback: try utf-8 with replacement
            try:
                text = raw_bytes.decode("utf-8", errors="replace")
            except Exception:
                return ToolResult.error(
                    f"Unable to decode file '{path}' (detected encoding: {encoding})"
                )

        lines = text.splitlines()
        total_lines = len(lines)

        # Slice to requested range (offset is 1-based)
        start = max(0, offset - 1)
        end = start + limit
        selected = lines[start:end]

        # Format with line numbers (cat -n style)
        formatted_lines: list[str] = []
        for i, line in enumerate(selected, start=start + 1):
            if len(line) > _MAX_LINE_LENGTH:
                line = line[:_MAX_LINE_LENGTH] + "… (truncated)"
            formatted_lines.append(f"{i:>6}\t{line}")

        header = f"File: {path} ({total_lines} lines"
        if start > 0 or end < total_lines:
            header += f", showing {start + 1}–{min(end, total_lines)}"
        header += ")\n"

        # Mark as read
        self.context.mark_read(str(path))

        return ToolResult.success(header + "\n".join(formatted_lines))

    async def _read_image(self, path: Path) -> ToolResult:
        """Read an image file and return base64 content blocks."""
        try:
            raw_bytes = path.read_bytes()
        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except OSError as exc:
            return ToolResult.error(f"Cannot read image: {exc}")

        mime = _IMAGE_MIME.get(path.suffix.lower(), "image/png")
        encoded = base64.standard_b64encode(raw_bytes).decode("ascii")

        # Mark as read
        self.context.mark_read(str(path))

        return ToolResult(content=[
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime,
                    "data": encoded,
                },
            },
            {"type": "text", "text": f"Image file: {path} ({len(raw_bytes)} bytes)"},
        ])

    async def _read_pdf(self, path: Path) -> ToolResult:
        """Read a PDF and return page content.

        Requires PyPDF2 or falls back to a stub message.
        """
        try:
            from PyPDF2 import PdfReader  # type: ignore[import-untyped]
        except ImportError:
            return ToolResult.error(
                "PDF reading requires PyPDF2. Install with: pip install PyPDF2"
            )

        try:
            reader = PdfReader(str(path))
            pages: list[str] = []
            for i, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                pages.append(f"--- Page {i} ---\n{text}")

            self.context.mark_read(str(path))
            return ToolResult.success(
                f"PDF: {path} ({len(reader.pages)} pages)\n\n" + "\n\n".join(pages)
            )
        except Exception as exc:
            return ToolResult.error(f"Failed to read PDF: {exc}")
