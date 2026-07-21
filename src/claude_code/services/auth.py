"""OAuth and API key authentication manager.

Handles credential storage and retrieval from multiple sources:
1. ``ANTHROPIC_API_KEY`` environment variable
2. ``~/.claude/credentials.json`` (OAuth tokens)
3. ``~/.claude/.credentials.json`` (service account tokens)

Credentials are stored with restrictive file permissions (0600)
for security.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import webbrowser
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default paths
CLAUDE_DIR = Path.home() / ".claude"
CREDENTIALS_PATH = CLAUDE_DIR / "credentials.json"
SERVICE_CREDENTIALS_PATH = CLAUDE_DIR / ".credentials.json"

# File permissions: owner read/write only
_SECURE_PERMISSIONS = stat.S_IRUSR | stat.S_IWUSR  # 0o600

# OAuth configuration
OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
OAUTH_TOKEN_URL = "https://claude.ai/oauth/token"
OAUTH_CLIENT_ID = "claude-code-py"
OAUTH_SCOPES = "openid profile email"


class AuthError(Exception):
    """Raised when authentication fails or credentials are invalid."""


class AuthManager:
    """Manages API keys and OAuth tokens for Claude API access.

    Supports multiple credential sources with a clear priority order:
    1. Explicit API key passed to constructor or ``get_api_key(key=...)``
    2. ``ANTHROPIC_API_KEY`` environment variable
    3. ``~/.claude/credentials.json`` (OAuth)
    4. ``~/.claude/.credentials.json`` (service account)

    Usage::

        auth = AuthManager()
        api_key = auth.get_api_key()
        if not api_key:
            auth.login()
            api_key = auth.get_api_key()
    """

    def __init__(
        self,
        credentials_path: Path | None = None,
        service_credentials_path: Path | None = None,
    ) -> None:
        """Initialize the auth manager.

        Args:
            credentials_path: Override path for OAuth credentials.
            service_credentials_path: Override path for service credentials.
        """
        self._credentials_path = credentials_path or CREDENTIALS_PATH
        self._service_credentials_path = (
            service_credentials_path or SERVICE_CREDENTIALS_PATH
        )

    # -- Public API --

    def get_api_key(self, key: str | None = None) -> str | None:
        """Return an API key from the first available source.

        Priority order:
        1. ``key`` parameter (explicit)
        2. ``ANTHROPIC_API_KEY`` environment variable
        3. OAuth token from credentials file
        4. Service account token from credentials file

        Args:
            key: An explicit API key to use, bypassing all other sources.

        Returns:
            An API key string, or ``None`` if no key is available.
        """
        # 1. Explicit key
        if key:
            return key

        # 2. Environment variable
        env_key = os.environ.get("ANTHROPIC_API_KEY")
        if env_key:
            return env_key

        # 3. OAuth credentials
        oauth_key = self._read_credential(self._credentials_path)
        if oauth_key:
            return oauth_key

        # 4. Service account credentials
        svc_key = self._read_credential(self._service_credentials_path)
        if svc_key:
            return svc_key

        return None

    def status(self) -> dict[str, Any]:
        """Check the current authentication state.

        Returns:
            A dict with keys:
            - ``authenticated`` (bool): Whether any credential is available.
            - ``source`` (str): Which source provided the key
              (``"env"``, ``"oauth"``, ``"service"``, ``"explicit"``, or ``"none"``).
            - ``key_preview`` (str): First 8 chars of the key for display.
        """
        env_key = os.environ.get("ANTHROPIC_API_KEY")
        if env_key:
            return {
                "authenticated": True,
                "source": "env",
                "key_preview": _preview(env_key),
            }

        oauth_key = self._read_credential(self._credentials_path)
        if oauth_key:
            return {
                "authenticated": True,
                "source": "oauth",
                "key_preview": _preview(oauth_key),
            }

        svc_key = self._read_credential(self._service_credentials_path)
        if svc_key:
            return {
                "authenticated": True,
                "source": "service",
                "key_preview": _preview(svc_key),
            }

        return {
            "authenticated": False,
            "source": "none",
            "key_preview": "",
        }

    async def login(self) -> str:
        """Open a browser for OAuth login and store the resulting token.

        This is a simplified OAuth flow that:
        1. Opens the authorization URL in the user's browser
        2. Expects the user to copy the resulting token
        3. Stores it in ``~/.claude/credentials.json``

        Returns:
            The stored API key / token.

        Raises:
            AuthError: If the credential cannot be stored.
        """
        auth_url = (
            f"{OAUTH_AUTHORIZE_URL}"
            f"?client_id={OAUTH_CLIENT_ID}"
            f"&scope={OAUTH_SCOPES}"
            f"&response_type=code"
        )

        logger.info("Opening browser for authentication: %s", auth_url)
        webbrowser.open(auth_url)

        # In a full implementation this would run a local HTTP server to
        # receive the OAuth callback. For now we prompt the user to paste
        # the code, which is then exchanged for a token.
        # This keeps the implementation simple and avoids port-binding issues.
        token = await self._prompt_for_token(auth_url)
        self._store_credential(self._credentials_path, token)
        logger.info("Credentials stored in %s", self._credentials_path)
        return token

    def logout(self) -> bool:
        """Remove stored OAuth credentials.

        Does not affect environment variables or service account credentials.

        Returns:
            ``True`` if credentials were removed, ``False`` if none existed.
        """
        removed = False
        if self._credentials_path.exists():
            self._credentials_path.unlink()
            logger.info("Removed OAuth credentials: %s", self._credentials_path)
            removed = True
        return removed

    def setup_token(self, token: str) -> Path:
        """Store a long-lived token for CI / non-interactive use.

        Writes the token to ``~/.claude/credentials.json`` with
        restrictive permissions.

        Args:
            token: The API key or token to store.

        Returns:
            Path to the credentials file.

        Raises:
            AuthError: If the file cannot be written.
        """
        self._store_credential(self._credentials_path, token)
        logger.info("Token stored for CI use in %s", self._credentials_path)
        return self._credentials_path

    # -- Internal helpers --

    def _read_credential(self, path: Path) -> str | None:
        """Read an API key from a credentials file.

        The file may contain either:
        - A JSON object with an ``api_key`` or ``token`` field
        - A plain-text token

        Args:
            path: Path to the credentials file.

        Returns:
            The API key string, or ``None`` if the file doesn't exist
            or cannot be read.
        """
        if not path.exists():
            return None

        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Failed to read credentials from %s: %s", path, exc)
            return None

        if not content:
            return None

        # Try JSON format first
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data.get("api_key") or data.get("token")
        except json.JSONDecodeError:
            pass

        # Plain text token
        return content

    def _store_credential(self, path: Path, token: str) -> None:
        """Store a credential in a JSON file with secure permissions.

        Creates the parent directory if it doesn't exist. Sets file
        permissions to 0600 (owner read/write only).

        Args:
            path: Destination file path.
            token: The token/key to store.

        Raises:
            AuthError: If the file cannot be written.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)

            data = {"api_key": token}
            # Create the file with 0600 from the start so the token is never
            # briefly world-readable between write and chmod.
            fd = os.open(
                str(path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                _SECURE_PERMISSIONS,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2))

            # O_CREAT's mode only applies to a newly-created file; enforce
            # secure permissions in case the file already existed.
            os.chmod(path, _SECURE_PERMISSIONS)
        except OSError as exc:
            raise AuthError(
                f"Failed to store credentials in {path}: {exc}"
            ) from exc

    async def _prompt_for_token(self, auth_url: str) -> str:
        """Prompt the user to paste an OAuth token or authorization code.

        In non-interactive environments this will fail gracefully.

        Args:
            auth_url: The authorization URL shown to the user.

        Returns:
            The token string entered by the user.

        Raises:
            AuthError: If no token is provided.
        """
        try:
            # Use input() as a fallback for terminal environments
            token = input(
                "\nAfter authorizing in the browser, paste the token or "
                "authorization code here:\n> "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            raise AuthError("Authentication cancelled by user.")

        if not token:
            raise AuthError("No token provided. Authentication failed.")

        return token


def _preview(key: str, length: int = 8) -> str:
    """Return a safe preview string showing only the first few characters.

    Args:
        key: The full key string.
        length: Number of characters to show.

    Returns:
        A preview like ``"sk-ant-a..."``.
    """
    if len(key) <= length:
        return key + "..."
    return key[:length] + "..."
