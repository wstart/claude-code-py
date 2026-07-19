"""Thin application orchestrator.

ClaudeApp wires together config, provider, tools, query engine,
UI, and sessions. Heavy work is delegated to:

- ``tools.factory`` — lazy tool registration
- ``core.slash`` — slash command dispatch
- ``core.loop_mode`` — /loop foreground execution
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import Any, Optional

from claude_code.core.config import AppConfig, load_config
from claude_code.core.message import Conversation, Message
from claude_code.core.query_engine import QueryEngine, QueryEngineCallbacks
from claude_code.core.session import Session, SessionManager
from claude_code.core.store import Store

logger = logging.getLogger(__name__)


class ClaudeApp:
    """Top-level application orchestrator — thin coordination layer."""

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config = config or load_config()
        self.store = Store()
        self.session_manager = SessionManager(
            working_directory=self.config.working_directory,
        )
        self.session: Optional[Session] = None
        self.query_engine: Optional[QueryEngine] = None
        self.permission_manager: Any = None
        self.hook_manager: Any = None
        self.ui: Any = None

        # Lazy provider
        self._provider: Any = None

        self._running = False
        self._loop_active = False
        self._loop_desc = ""

    # ------------------------------------------------------------------
    # Lazy provider
    # ------------------------------------------------------------------

    @property
    def provider(self) -> Any:
        if self._provider is None:
            self._provider = self._create_provider()
        return self._provider

    def _lazy_provider(self) -> Any:
        """Return a proxy that defers provider creation to first use."""
        app = self

        class _LazyProvider:
            def __getattr__(self, name):
                return getattr(app.provider, name)

            async def create_message(self, *args, **kwargs):
                return await app.provider.create_message(*args, **kwargs)

            async def close(self):
                if app._provider:
                    await app._provider.close()

            @property
            def provider_name(self):
                return app.provider.provider_name

        return _LazyProvider()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Initialize subsystems. Provider is deferred to first use."""
        # Tools — lazy factory
        from claude_code.tools.factory import create_tool_registry
        from claude_code.tools.base import ToolContext

        ctx = ToolContext(cwd=self.config.working_directory)
        self.tool_registry = create_tool_registry(ctx)

        # Permissions + Hooks (lazy imports)
        from claude_code.permissions.manager import PermissionManager
        from claude_code.hooks.manager import HookManager

        self.permission_manager = PermissionManager(config=self.config)
        self.hook_manager = HookManager(config=getattr(self.config, "hooks", {}))

        # Query engine — provider deferred to first API call
        self.query_engine = QueryEngine(
            provider=self._lazy_provider(),
            tool_registry=self.tool_registry,
            config=self.config,
        )

        # Session
        self.session = self.session_manager.create_session(
            model=self.config.model or "claude-sonnet-4-20250514",
        )

    async def teardown(self) -> None:
        self._running = False
        if self._provider:
            await self._provider.close()
        if self.session:
            try:
                self.session_manager.save_session(self.session)
            except Exception:
                logger.exception("Error saving session")

    # ------------------------------------------------------------------
    # Run modes
    # ------------------------------------------------------------------

    async def run_interactive(
        self,
        resume: bool = False,
        resume_session_id: Optional[str] = None,
    ) -> None:
        """Run the interactive terminal session."""
        await self.setup()

        if resume:
            await self.resume_session()
        elif resume_session_id:
            await self.resume_session(resume_session_id)

        from claude_code.ui.app_ui import AppUI
        from claude_code.core.slash import SlashDispatcher

        self.ui = AppUI()
        self.ui.update_status(
            model=self.config.model or "claude-sonnet-4-20250514",
            session_id=self.session.metadata.id if self.session else "unknown",
            working_directory=self.config.working_directory,
        )

        self.slash = SlashDispatcher(self)

        assert self.query_engine is not None
        self.query_engine.callbacks = QueryEngineCallbacks(
            on_stream_text=lambda t: self.ui and self.ui.show_stream(t),
            on_tool_call=lambda n, p: self.ui and self.ui.show_tool_call(n, p),
            on_tool_result=lambda n, r: self.ui and self.ui.show_tool_result(
                n, r.content if isinstance(r.content, str) else str(r.content),
                is_error=r.is_error,
            ),
            on_error=lambda m: self.ui and self.ui.show_error(m),
            on_usage=lambda u: self.ui and self.ui.update_status(
                tokens_used=u.get("input_tokens", 0) + u.get("output_tokens", 0)
            ),
            on_spinner_show=lambda l: self.ui and self.ui.show_spinner(l),
            on_spinner_hide=lambda: self.ui and self.ui.hide_spinner(),
        )

        self._running = True

        def _sigint(sig, frame):
            if self._loop_active:
                self._loop_active = False
            else:
                self._running = False
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _sigint)

        try:
            self.ui._print_welcome()

            while self._running:
                if self._loop_active:
                    from claude_code.core.loop_mode import run_loop_mode
                    try:
                        await run_loop_mode(self)
                    except KeyboardInterrupt:
                        self._loop_active = False
                        self.ui.show_info("Loop stopped.")
                    continue

                try:
                    user_input = await self.ui.get_input()
                except (EOFError, KeyboardInterrupt):
                    break

                if user_input is None:
                    break
                user_input = user_input.strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    if not await self.slash.handle(user_input):
                        self.ui.show_info(f"Unknown command: {user_input}")
                    continue

                try:
                    result = await self.query_engine.run(user_input)
                except KeyboardInterrupt:
                    self.query_engine.cancel()
                    self.ui.end_stream()
                    self.ui.show_info("Cancelled.")
                    continue

                had_stream = self.ui._streaming
                self.ui.end_stream()
                if result.text and not had_stream:
                    self.ui.display_assistant_message(result.text)

                if self.session:
                    self.session.add_message("user", user_input)
                    if result.text:
                        self.session.add_message("assistant", result.text)
                    self.session_manager.save_session(self.session)

                if self.query_engine:
                    cost = self.query_engine.get_cost_info()
                    self.ui.update_status(
                        model=self.config.model, tokens_used=cost.total_tokens,
                    )
                if result.error:
                    self.ui.show_error(result.error)

        except KeyboardInterrupt:
            pass
        finally:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            if self.ui:
                self.ui.shutdown()
            await self.teardown()

    async def run_print(self, query: str) -> str:
        """Non-interactive print mode."""
        await self.setup()
        assert self.query_engine is not None

        self.query_engine.callbacks = QueryEngineCallbacks(
            on_stream_text=lambda t: (sys.stdout.write(t), sys.stdout.flush()),
            on_tool_call=lambda n, p: sys.stderr.write(f"\n⚡ {n}\n"),
            on_error=lambda m: sys.stderr.write(f"Error: {m}\n"),
        )

        try:
            result = await self.query_engine.run(query)
            if result.text and not result.text.endswith("\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
            return result.text
        finally:
            await self.teardown()

    async def resume_session(self, session_id: Optional[str] = None) -> None:
        if session_id:
            loaded = self.session_manager.load_session(session_id)
        else:
            loaded = self.session_manager.get_latest_session()
        if loaded is None:
            return

        self.session = loaded
        if self.query_engine:
            conv = Conversation()
            for msg_data in loaded.messages:
                role = msg_data.get("role", "user")
                content = msg_data.get("content", "")
                msg = (Message.user(content) if role == "user" else Message.assistant(content)) if isinstance(content, str) else Message(role=role, content=content)
                conv.add_message(msg)
                if self.ui:
                    text = content if isinstance(content, str) else str(content)
                    (self.ui.display_user_message if role == "user" else self.ui.display_assistant_message)(text)
            self.query_engine.conversation = conv

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    def _create_provider(self) -> Any:
        provider_name = self.config.provider or "anthropic"
        model = self.config.model
        if provider_name == "openai":
            from claude_code.providers.openai_compat import OpenAICompatProvider
            return OpenAICompatProvider(
                api_key=self.config.api_key or os.environ.get("OPENAI_API_KEY", ""),
                base_url=self.config.base_url or "https://api.openai.com/v1",
                default_model=model,
            )
        from claude_code.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(
            api_key=self.config.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=self.config.base_url,
            default_model=model,
        )
