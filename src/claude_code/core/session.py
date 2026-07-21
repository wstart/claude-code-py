"""Session management for persisting conversation state.

Sessions are stored as JSON files under
``~/.claude/projects/{project-hash}/sessions/``. Each session
contains conversation messages, metadata, and timestamps.
"""

import hashlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def _project_hash(working_directory: str) -> str:
    """Compute a short hash for a project directory.

    Args:
        working_directory: Absolute path to the project root.

    Returns:
        First 12 hex characters of the SHA-256 hash.
    """
    normalized = os.path.realpath(working_directory)
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


@dataclass
class SessionMetadata:
    """Metadata about a session."""

    id: str
    name: str
    created_at: float
    updated_at: float
    model: str
    message_count: int
    working_directory: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionMetadata":
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            model=data.get("model", ""),
            message_count=data.get("message_count", 0),
            working_directory=data.get("working_directory", ""),
        )


@dataclass
class Session:
    """A conversation session with messages and metadata."""

    metadata: SessionMetadata
    messages: list[dict[str, Any]] = field(default_factory=list)

    def add_message(self, role: str, content: Any, **kwargs: Any) -> None:
        """Add a message to the session.

        Args:
            role: Message role (user, assistant, system).
            content: Message content (string or list of content blocks).
            **kwargs: Additional fields (e.g., tool_use_id).
        """
        msg: dict[str, Any] = {"role": role, "content": content}
        msg.update(kwargs)
        self.messages.append(msg)
        self.metadata.message_count = len(self.messages)
        self.metadata.updated_at = time.time()

    def replace_messages(self, messages: list[dict[str, Any]]) -> None:
        """Replace all stored messages with API-format messages.

        Used to persist the full conversation (including tool_use and
        tool_result blocks) rather than a text-only summary, so a resumed
        session keeps its complete tool history.
        """
        self.messages = list(messages)
        self.metadata.message_count = len(self.messages)
        self.metadata.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full session to a dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        """Deserialize a session from a dictionary."""
        meta = SessionMetadata.from_dict(data["metadata"])
        return cls(
            metadata=meta,
            messages=data.get("messages", []),
        )


class SessionManager:
    """Manages session persistence on disk.

    Sessions are stored as JSON files in a directory derived from
    the project's working directory.

    Attributes:
        working_directory: The project root for session storage.
    """

    def __init__(self, working_directory: str | None = None) -> None:
        """Initialize the session manager.

        Args:
            working_directory: Project root. Defaults to cwd.
        """
        self.working_directory = working_directory or os.getcwd()
        self._sessions_dir = self._get_sessions_dir()

    def _get_sessions_dir(self) -> Path:
        """Get (and create) the sessions directory.

        Returns:
            Path to the sessions directory.
        """
        base = Path.home() / ".claude" / "projects" / _project_hash(self.working_directory)
        sessions_dir = base / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        return sessions_dir

    def create_session(
        self,
        model: str = "",
        name: str = "",
    ) -> Session:
        """Create a new session.

        Args:
            model: Model name to record in metadata.
            name: Optional human-readable session name.

        Returns:
            A new Session instance.
        """
        now = time.time()
        session_id = uuid.uuid4().hex

        metadata = SessionMetadata(
            id=session_id,
            name=name or f"session-{session_id[:8]}",
            created_at=now,
            updated_at=now,
            model=model,
            message_count=0,
            working_directory=self.working_directory,
        )
        return Session(metadata=metadata)

    def save_session(self, session: Session) -> Path:
        """Persist a session to disk.

        Args:
            session: The Session to save.

        Returns:
            Path to the saved JSON file.
        """
        session.metadata.updated_at = time.time()
        filepath = self._sessions_dir / f"{session.metadata.id}.json"
        filepath.write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return filepath

    def load_session(self, session_id: str) -> Session | None:
        """Load a session by ID.

        Args:
            session_id: The session UUID.

        Returns:
            The Session if found, None otherwise.
        """
        filepath = self._sessions_dir / f"{session_id}.json"
        if not filepath.exists():
            return None
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            return Session.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def list_sessions(self, limit: int = 20) -> list[SessionMetadata]:
        """List recent sessions for the current project.

        Args:
            limit: Maximum number of sessions to return.

        Returns:
            List of SessionMetadata, sorted by updated_at descending.
        """
        sessions: list[SessionMetadata] = []
        for filepath in self._sessions_dir.glob("*.json"):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                meta = SessionMetadata.from_dict(data["metadata"])
                sessions.append(meta)
            except (json.JSONDecodeError, KeyError):
                continue

        sessions.sort(key=lambda s: s.updated_at, reverse=True)
        return sessions[:limit]

    def get_latest_session(self) -> Session | None:
        """Get the most recently updated session.

        Returns:
            The latest Session, or None if no sessions exist.
        """
        sessions = self.list_sessions(limit=1)
        if not sessions:
            return None
        return self.load_session(sessions[0].id)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file.

        Args:
            session_id: The session UUID.

        Returns:
            True if the session was deleted, False if not found.
        """
        filepath = self._sessions_dir / f"{session_id}.json"
        if filepath.exists():
            filepath.unlink()
            return True
        return False
