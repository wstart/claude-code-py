"""Auto-update checker for the claude-code package.

Checks PyPI for newer versions and optionally installs updates via pip.
Uses a non-blocking HTTP call and caches results to avoid hammering
the index on every startup.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from claude_code.utils.logging import get_logger
from claude_code.utils.path_utils import expand_user

logger = get_logger("services.updater")

_PYPI_URL = "https://pypi.org/pypi/claude-code-py/json"
_CACHE_PATH = expand_user("~/.claude/update_check.json")
_CACHE_TTL_SECONDS = 3600 * 6  # 6 hours between checks

_PACKAGE_NAME = "claude-code-py"


@dataclass(frozen=True)
class UpdateInfo:
    """Information about an available update."""

    current_version: str
    latest_version: str
    release_date: str
    summary: str
    url: str


class UpdateChecker:
    """Check for package updates on PyPI.

    Parameters
    ----------
    current_version:
        The currently installed version string (e.g. ``"0.3.1"``).
    """

    def __init__(self, current_version: str) -> None:
        self.current_version = current_version
        self._cached_info: Optional[UpdateInfo] = None

    async def check(self) -> Optional[UpdateInfo]:
        """Check PyPI for a newer version.

        Returns cached result if a check was done within the TTL window.

        Returns:
            :class:`UpdateInfo` if a newer version exists, ``None`` otherwise.
        """
        # Check cache first
        cached = self._load_cache()
        if cached is not None:
            self._cached_info = cached
            return cached

        try:
            info = await self._fetch_latest()
        except Exception as exc:
            logger.debug(f"Update check failed: {exc}")
            return None

        if info is not None and self._is_newer(info.latest_version, self.current_version):
            self._save_cache(info)
            self._cached_info = info
            return info

        # Cache a "no update" result too (to avoid repeated checks)
        self._save_cache(None)
        return None

    async def install(self, version: Optional[str] = None) -> bool:
        """Install an update via pip.

        Args:
            version: Specific version to install. If ``None``, installs latest.

        Returns:
            ``True`` if the install command succeeded.
        """
        target = f"{_PACKAGE_NAME}=={version}" if version else _PACKAGE_NAME
        cmd = ["pip", "install", "--upgrade", target]

        logger.info(f"Installing update: {' '.join(cmd)}")

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                ),
            )
            if result.returncode == 0:
                logger.info("Update installed successfully.")
                return True
            logger.error(f"pip install failed: {result.stderr.strip()}")
            return False
        except subprocess.TimeoutExpired:
            logger.error("pip install timed out.")
            return False
        except FileNotFoundError:
            logger.error("pip not found. Install manually.")
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_latest(self) -> Optional[UpdateInfo]:
        """Fetch latest version info from PyPI."""
        import urllib.request

        loop = asyncio.get_running_loop()

        def _do_fetch() -> Optional[UpdateInfo]:
            try:
                req = urllib.request.Request(
                    _PYPI_URL,
                    headers={"Accept": "application/json", "User-Agent": "claude-code-py"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                info_data = data.get("info", {})
                latest_version = info_data.get("version", "")
                releases = data.get("releases", {})

                # Find release date for the latest version
                release_date = ""
                if latest_version in releases and releases[latest_version]:
                    upload_time = releases[latest_version][0].get("upload_time_iso_8601", "")
                    release_date = upload_time[:10] if upload_time else ""

                return UpdateInfo(
                    current_version=self.current_version,
                    latest_version=latest_version,
                    release_date=release_date,
                    summary=info_data.get("summary", ""),
                    url=info_data.get("project_url", f"https://pypi.org/project/{_PACKAGE_NAME}/"),
                )
            except Exception as exc:
                logger.debug(f"PyPI fetch error: {exc}")
                return None

        return await loop.run_in_executor(None, _do_fetch)

    @staticmethod
    def _is_newer(remote: str, local: str) -> bool:
        """Compare version strings (simple numeric comparison)."""
        def _parse(v: str) -> tuple[int, ...]:
            parts = []
            for segment in v.strip().split("."):
                try:
                    parts.append(int(segment))
                except ValueError:
                    parts.append(0)
            return tuple(parts)

        return _parse(remote) > _parse(local)

    def _load_cache(self) -> Optional[UpdateInfo]:
        """Load cached update info if still within TTL."""
        if not _CACHE_PATH.exists():
            return None
        try:
            raw = _CACHE_PATH.read_text(encoding="utf-8")
            cache = json.loads(raw)
            checked_at = cache.get("checked_at", 0)
            if time.time() - checked_at > _CACHE_TTL_SECONDS:
                return None

            data = cache.get("update_info")
            if data is None:
                return None

            return UpdateInfo(**data)
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            return None

    def _save_cache(self, info: Optional[UpdateInfo]) -> None:
        """Save update check result to cache file."""
        try:
            _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            cache_data = {
                "checked_at": time.time(),
                "current_version": self.current_version,
                "update_info": {
                    "current_version": info.current_version,
                    "latest_version": info.latest_version,
                    "release_date": info.release_date,
                    "summary": info.summary,
                    "url": info.url,
                } if info else None,
            }
            _CACHE_PATH.write_text(json.dumps(cache_data), encoding="utf-8")
        except OSError as exc:
            logger.debug(f"Failed to save update cache: {exc}")
