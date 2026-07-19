"""Central application orchestrator.

:class:`ClaudeApp` is the top-level entry point that ties together:

- Configuration loading
- LLM provider initialization
- Tool registration
- Query engine (agentic loop)
- Terminal UI
- Session management
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

from claude_code.core.config import AppConfig, load_config
from claude_code.core.context import compress, needs_compression
from claude_code.core.message import Conversation, Message
from claude_code.core.query_engine import QueryEngine, QueryEngineCallbacks, QueryResult
from claude_code.core.session import Session, SessionManager
from claude_code.core.store import AppState, Store
from claude_code.providers.base import BaseProvider
from claude_code.tools.ask_user import AskUserTool
from claude_code.tools.bash import BashTool
from claude_code.tools.diagnostics import DiagnosticsTool
from claude_code.tools.edit import EditTool
from claude_code.tools.execute_code import ExecuteCodeTool
from claude_code.tools.exit_plan_mode import ExitPlanModeTool
from claude_code.tools.glob_tool import GlobTool
from claude_code.tools.grep import GrepTool
from claude_code.tools.ls import LSTool
from claude_code.tools.multi_edit import MultiEditTool
from claude_code.tools.notebook_edit import NotebookEditTool
from claude_code.tools.notebook_read import NotebookReadTool
from claude_code.tools.read import ReadTool
from claude_code.tools.registry import ToolContext, ToolRegistry
from claude_code.tools.task import TaskTool
from claude_code.tools.todo_read import TodoReadTool
from claude_code.tools.todo_write import TodoWriteTool
from claude_code.tools.web_fetch import WebFetchTool
from claude_code.tools.web_search import WebSearchTool
from claude_code.tools.write import WriteTool

logger = logging.getLogger(__name__)


class ClaudeApp:
    """Top-level application orchestrator.

    Parameters
    ----------
    config:
        Optional pre-built config. If *None*, ``load_config()`` is called.
    """

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        self.config = config or load_config()
        self.store = Store()
        self.session_manager = SessionManager(
            working_directory=self.config.working_directory,
        )
        self.session: Optional[Session] = None
        self.provider: Optional[BaseProvider] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.query_engine: Optional[QueryEngine] = None
        self.permission_manager: Any = None
        self.hook_manager: Any = None
        self.ui: Any = None  # AppUI — lazy to avoid import cycles

        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Initialize all subsystems."""
        logger.info("Setting up Claude Code Py...")

        # Provider
        self.provider = self._create_provider()

        # Tools
        self.tool_registry = self._create_tool_registry()

        # Permissions
        from claude_code.permissions.manager import PermissionManager

        self.permission_manager = PermissionManager(config=self.config)

        # Hooks
        from claude_code.hooks.manager import HookManager

        self.hook_manager = HookManager(config=getattr(self.config, "hooks", {}))

        # Query engine
        self.query_engine = QueryEngine(
            provider=self.provider,
            tool_registry=self.tool_registry,
            config=self.config,
        )

        # Session
        self.session = self.session_manager.create_session(
            model=self.config.model or "claude-sonnet-4-20250514",
        )

        # Store
        await self.store.update_state({
            "working_directory": self.config.working_directory,
            "model": self.config.model,
            "permission_mode": self.config.permission_mode,
        })

        logger.info("Setup complete.")

    async def teardown(self) -> None:
        """Clean up resources."""
        self._running = False

        if self.provider:
            await self.provider.close()

        if self.tool_registry:
            for tool in self.tool_registry.list_all():
                close_fn = getattr(tool, "close", None)
                if close_fn and callable(close_fn):
                    try:
                        if asyncio.iscoroutinefunction(close_fn):
                            await close_fn()
                        else:
                            close_fn()
                    except Exception:
                        logger.exception("Error closing tool %s", tool.name)

        if self.session:
            try:
                self.session_manager.save_session(self.session)
            except Exception:
                logger.exception("Error saving session")

        logger.info("Teardown complete.")

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

        # Resume previous session if requested
        if resume:
            await self.resume_session()
        elif resume_session_id:
            await self.resume_session(resume_session_id)

        # Lazy import UI
        from claude_code.ui.app_ui import AppUI

        self.ui = AppUI()

        # Set initial status
        self.ui.update_status(
            model=self.config.model or "claude-sonnet-4-20250514",
            session_id=self.session.metadata.id if self.session else "unknown",
            working_directory=self.config.working_directory,
        )

        # Wire query engine callbacks → UI
        assert self.query_engine is not None
        self.query_engine.callbacks = QueryEngineCallbacks(
            on_stream_text=self._cb_stream_text,
            on_tool_call=self._cb_tool_call,
            on_tool_result=self._cb_tool_result,
            on_error=self._cb_error,
            on_usage=self._cb_usage,
            on_spinner_show=self._cb_spinner_show,
            on_spinner_hide=self._cb_spinner_hide,
        )

        self._running = True

        try:
            self.ui._print_welcome()

            while self._running:
                try:
                    user_input = await self.ui.get_input()
                except (EOFError, KeyboardInterrupt):
                    break

                if user_input is None:
                    break

                user_input = user_input.strip()
                if not user_input:
                    continue

                # Slash commands
                if user_input.startswith("/"):
                    handled = await self._handle_slash_command(user_input)
                    if not handled:
                        self.ui.show_info(f"Unknown command: {user_input}")
                    continue

                # Display user message
                self.ui.display_user_message(user_input)

                # Run through query engine
                result = await self.query_engine.run(user_input)

                # Check if streaming displayed anything, then end stream
                had_stream = self.ui._streaming
                self.ui.end_stream()

                # Fallback: if streaming didn't show anything, print directly
                if result.text and not had_stream:
                    self.ui.display_assistant_message(result.text)

                # Persist to session
                if self.session:
                    self.session.add_message("user", user_input)
                    if result.text:
                        self.session.add_message("assistant", result.text)
                    self.session_manager.save_session(self.session)

                # Update status bar
                self._update_status_bar()

                if result.error:
                    self.ui.show_error(result.error)

        finally:
            if self.ui:
                self.ui.shutdown()
            await self.teardown()

    async def run_print(self, query: str) -> str:
        """Non-interactive print mode — returns the assistant's text."""
        await self.setup()
        assert self.query_engine is not None

        def _print_stream(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        def _print_tool_call(name: str, params: dict[str, Any]) -> None:
            sys.stderr.write(f"\n⚡ {name}\n")
            sys.stderr.flush()

        self.query_engine.callbacks = QueryEngineCallbacks(
            on_stream_text=_print_stream,
            on_tool_call=_print_tool_call,
            on_error=lambda msg: sys.stderr.write(f"Error: {msg}\n"),
        )

        try:
            result = await self.query_engine.run(query)
            text = result.text
            if text and not text.endswith("\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
            return text
        finally:
            await self.teardown()

    async def resume_session(self, session_id: Optional[str] = None) -> None:
        """Resume a previous session."""
        if session_id:
            loaded = self.session_manager.load_session(session_id)
        else:
            loaded = self.session_manager.get_latest_session()

        if loaded is None:
            logger.warning("No session found to resume.")
            return

        self.session = loaded

        if self.query_engine:
            conv = Conversation()
            for msg_data in loaded.messages:
                role = msg_data.get("role", "user")
                content = msg_data.get("content", "")
                if isinstance(content, str):
                    msg = Message.user(content) if role == "user" else Message.assistant(content)
                else:
                    msg = Message(role=role, content=content)
                conv.add_message(msg)
                # Display history in UI
                if self.ui:
                    text = content if isinstance(content, str) else str(content)
                    if role == "user":
                        self.ui.display_user_message(text)
                    elif role == "assistant":
                        self.ui.display_assistant_message(text)
            self.query_engine.conversation = conv

        logger.info("Resumed session: %s (%d messages)", loaded.metadata.id, len(loaded.messages))

    # ------------------------------------------------------------------
    # UI callbacks (thin wrappers that guard against None ui)
    # ------------------------------------------------------------------

    def _cb_stream_text(self, text: str) -> None:
        if self.ui:
            self.ui.show_stream(text)

    def _cb_tool_call(self, name: str, params: dict[str, Any]) -> None:
        if self.ui:
            self.ui.show_tool_call(name, params)

    def _cb_tool_result(self, name: str, result: Any) -> None:
        if self.ui:
            content = result.content if isinstance(result.content, str) else str(result.content)
            self.ui.show_tool_result(name=name, result=content, is_error=result.is_error)

    def _cb_error(self, msg: str) -> None:
        if self.ui:
            self.ui.show_error(msg)

    def _cb_usage(self, usage: dict[str, int]) -> None:
        if self.ui:
            total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            self.ui.update_status(tokens_used=total)

    def _cb_spinner_show(self, label: str) -> None:
        if self.ui:
            self.ui.show_spinner(label)

    def _cb_spinner_hide(self) -> None:
        if self.ui:
            self.ui.hide_spinner()

    def _update_status_bar(self) -> None:
        if self.ui and self.query_engine:
            cost = self.query_engine.get_cost_info()
            self.ui.update_status(
                model=self.config.model,
                tokens_used=cost.total_tokens,
            )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    def _create_provider(self) -> BaseProvider:
        """Create the LLM provider based on config."""
        provider_name = self.config.provider or "anthropic"
        model = self.config.model

        if provider_name == "openai":
            from claude_code.providers.openai_compat import OpenAICompatProvider

            api_key = self.config.api_key or os.environ.get("OPENAI_API_KEY", "")
            return OpenAICompatProvider(
                api_key=api_key,
                base_url=self.config.base_url or "https://api.openai.com/v1",
                default_model=model,
            )

        # Default: Anthropic
        from claude_code.providers.anthropic_provider import AnthropicProvider

        api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        return AnthropicProvider(
            api_key=api_key,
            base_url=self.config.base_url,
            default_model=model,
        )

    def _create_tool_registry(self) -> ToolRegistry:
        """Create and populate the tool registry with built-in tools."""
        ctx = ToolContext(cwd=self.config.working_directory)
        registry = ToolRegistry(context=ctx)

        # Phase 1: File + Shell tools
        registry.register(ReadTool(ctx))
        registry.register(WriteTool(ctx))
        registry.register(EditTool(ctx))
        registry.register(MultiEditTool(ctx))
        registry.register(GlobTool(ctx))
        registry.register(GrepTool(ctx))
        registry.register(LSTool(ctx))
        registry.register(BashTool(ctx))

        # Phase 2: Notebook, Web, Agent, Planning tools
        registry.register(NotebookReadTool(ctx))
        registry.register(NotebookEditTool(ctx))
        registry.register(WebFetchTool(ctx))
        registry.register(WebSearchTool(ctx))
        registry.register(TaskTool(ctx))
        registry.register(TodoReadTool(ctx))
        registry.register(TodoWriteTool(ctx))
        registry.register(ExitPlanModeTool(ctx))
        # IDE / Execution tools
        registry.register(DiagnosticsTool(ctx))
        registry.register(ExecuteCodeTool(ctx))
        registry.register(AskUserTool(ctx))

        return registry

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    async def _handle_slash_command(self, command: str) -> bool:
        """Handle a slash command. Returns True if handled."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "/help": self._cmd_help,
            "/?": self._cmd_help,
            "/clear": self._cmd_clear,
            "/compact": self._cmd_compact,
            "/cost": self._cmd_cost,
            "/usage": self._cmd_cost,
            "/model": self._cmd_model,
            "/status": self._cmd_status,
            "/config": self._cmd_config,
            "/settings": self._cmd_config,
            "/memory": self._cmd_memory,
            "/doctor": self._cmd_doctor,
            "/resume": self._cmd_resume,
            "/bug": self._cmd_bug,
            "/permissions": self._cmd_permissions,
            "/perms": self._cmd_permissions,
            "/hooks": self._cmd_hooks,
            "/mcp": self._cmd_mcp,
            "/plugins": self._cmd_plugins,
            "/agents": self._cmd_agents,
            "/review": self._cmd_review,
            "/init": self._cmd_init,
            "/add-dir": self._cmd_add_dir,
            "/rename": self._cmd_rename,
            "/exit": self._cmd_exit,
            "/quit": self._cmd_exit,
        }

        handler = handlers.get(cmd)
        if handler:
            handler(args)
            return True
        return False

    def _cmd_help(self, args: str = "") -> None:
        if not self.ui:
            return
        self.ui.show_info(
            "Commands\n\n"
            "  /help          Show this help\n"
            "  /clear         Clear conversation\n"
            "  /compact       Compress context to save tokens\n"
            "  /cost          Show token usage and cost\n"
            "  /model [name]  Show or change model\n"
            "  /status        Show session info\n"
            "  /config        Show current configuration\n"
            "  /memory        Show CLAUDE.md content\n"
            "  /resume        Resume most recent session\n"
            "  /doctor        Run diagnostics\n"
            "  /review        Code review current changes\n"
            "  /init          Initialize CLAUDE.md for project\n"
            "  /permissions   Show permission rules\n"
            "  /hooks         Show active hooks\n"
            "  /mcp           Show MCP servers\n"
            "  /plugins       Show installed plugins\n"
            "  /agents        Show running agents\n"
            "  /add-dir       Add directory to workspace\n"
            "  /bug           Report a bug\n"
            "  /exit          Exit Claude Code\n\n"
            "  Enter=submit  Esc+Enter=newline  Ctrl+C=cancel"
        )

    def _cmd_clear(self, args: str = "") -> None:
        if self.query_engine:
            self.query_engine.reset()
        if self.ui:
            self.ui.show_info("Conversation cleared.")

    def _cmd_compact(self, args: str = "") -> None:
        if not self.query_engine or not self.ui:
            return
        api_msgs = self.query_engine.conversation.to_api_messages()
        compressed = compress(api_msgs, max_tokens=self.config.max_tokens or 200_000)
        self.query_engine._rebuild_conversation(compressed)
        self.ui.show_info(f"Compacted: {len(api_msgs)} → {len(compressed)} messages")

    def _cmd_cost(self, args: str = "") -> None:
        if self.query_engine and self.ui:
            c = self.query_engine.get_cost_info()
            self.ui.show_info(
                f"Tokens — Input: {c.input_tokens:,}, Output: {c.output_tokens:,}, "
                f"Total: {c.total_tokens:,}"
            )

    def _cmd_model(self, args: str = "") -> None:
        if not self.ui:
            return
        if args:
            self.config.model = args
            self.ui.show_info(f"Model → {args}")
            self.ui.update_status(model=args)
        else:
            self.ui.show_info(f"Current model: {self.config.model}")

    def _cmd_status(self, args: str = "") -> None:
        if not self.ui:
            return
        cost = self.query_engine.get_cost_info() if self.query_engine else None
        info = (
            f"Session: {self.session.metadata.id if self.session else 'N/A'}\n"
            f"Model: {self.config.model}\n"
            f"Working dir: {self.config.working_directory}\n"
            f"Permission: {self.config.permission_mode}"
        )
        if cost:
            info += f"\nTokens: {cost.total_tokens:,}"
        self.ui.show_info(info)

    def _cmd_config(self, args: str = "") -> None:
        if self.ui:
            self.ui.show_info(
                f"Provider: {self.config.provider}\n"
                f"Model: {self.config.model}\n"
                f"Permission: {self.config.permission_mode}\n"
                f"Working dir: {self.config.working_directory}\n"
                f"Max tokens: {self.config.max_tokens}\n"
                f"Max turns: {self.config.max_turns}"
            )

    def _cmd_memory(self, args: str = "") -> None:
        if not self.ui:
            return
        md = self.config.claude_md_content
        self.ui.show_info(f"CLAUDE.md:\n\n{md}" if md else "No CLAUDE.md found.")

    def _cmd_doctor(self, args: str = "") -> None:
        if not self.ui:
            return
        checks: list[str] = []
        checks.append(f"✓ Python {sys.version.split()[0]}")

        if os.environ.get("ANTHROPIC_API_KEY"):
            checks.append("✓ ANTHROPIC_API_KEY is set")
        else:
            checks.append("✗ ANTHROPIC_API_KEY is not set")

        if self.provider:
            checks.append(f"✓ Provider: {self.provider.provider_name}")

        if self.tool_registry:
            names = self.tool_registry.list_names()
            checks.append(f"✓ Tools: {len(names)} ({', '.join(names)})")

        wd = Path(self.config.working_directory)
        checks.append(f"{'✓' if wd.exists() else '✗'} Working dir: {wd}")

        try:
            from claude_code.utils.git_utils import is_git_repo

            if is_git_repo(self.config.working_directory):
                checks.append("✓ Git repository")
        except Exception:
            pass

        try:
            from claude_code.utils.platform import has_command

            checks.append(f"{'✓' if has_command('rg') else '○'} ripgrep")
        except Exception:
            pass

        self.ui.show_info("Diagnostics:\n\n" + "\n".join(checks))

    def _cmd_exit(self, args: str = "") -> None:
        self._running = False

    def _cmd_resume(self, args: str = "") -> None:
        if not self.ui or not self.query_engine:
            return
        session_id = args.strip() if args.strip() else None
        if session_id:
            loaded = self.session_manager.load_session(session_id)
        else:
            loaded = self.session_manager.get_latest_session()
        if loaded is None:
            self.ui.show_info("No previous session found.")
            return
        self.session = loaded
        from claude_code.core.message import Conversation, Message
        from claude_code.ui.components.chat_display import DisplayMessage, MessageRole
        conv = Conversation()
        for msg_data in loaded.messages:
            role = msg_data.get("role", "user")
            content = msg_data.get("content", "")
            if isinstance(content, str):
                msg = Message.user(content) if role == "user" else Message.assistant(content)
            else:
                msg = Message(role=role, content=content)
            conv.add_message(msg)
            # Display the message in the UI
            text = content if isinstance(content, str) else str(content)
            if role == "user":
                self.ui.display_user_message(text)
            elif role == "assistant":
                self.ui.display_assistant_message(text)
        self.query_engine.conversation = conv
        self.ui.show_info(
            f"Resumed session {loaded.metadata.id[:8]}... "
            f"({len(loaded.messages)} messages)"
        )

    def _cmd_bug(self, args: str = "") -> None:
        if self.ui:
            self.ui.show_info(
                "Report bugs at: https://github.com/anthropics/claude-code/issues"
            )

    def _cmd_permissions(self, args: str = "") -> None:
        if not self.ui:
            return
        mode = self.config.permission_mode
        info = f"Permission mode: {mode}\n\n"
        if self.permission_manager:
            try:
                rules = self.permission_manager._rule_engine
                allow = [str(r) for r in getattr(rules, "_allow_rules", [])]
                deny = [str(r) for r in getattr(rules, "_deny_rules", [])]
                if allow:
                    info += f"Allow rules:\n  " + "\n  ".join(allow) + "\n"
                if deny:
                    info += f"Deny rules:\n  " + "\n  ".join(deny) + "\n"
                if not allow and not deny:
                    info += "No custom rules configured."
            except Exception:
                info += "Could not read rules."
        self.ui.show_info(info)

    def _cmd_hooks(self, args: str = "") -> None:
        if not self.ui:
            return
        if self.hook_manager:
            try:
                hooks = self.hook_manager.list_hooks()
                if hooks:
                    lines = []
                    for event, entries in hooks.items():
                        for e in entries:
                            lines.append(f"  {event}: {e.get('command', '?')}")
                    self.ui.show_info("Active hooks:\n" + "\n".join(lines))
                else:
                    self.ui.show_info("No hooks configured.")
            except Exception:
                self.ui.show_info("No hooks configured.")
        else:
            self.ui.show_info("Hook manager not initialized.")

    def _cmd_mcp(self, args: str = "") -> None:
        if not self.ui:
            return
        servers = self.config.mcp_servers if hasattr(self.config, "mcp_servers") else {}
        if servers:
            lines = [f"  {name}: {cfg}" for name, cfg in servers.items()]
            self.ui.show_info("MCP servers:\n" + "\n".join(lines))
        else:
            self.ui.show_info("No MCP servers configured.")

    def _cmd_plugins(self, args: str = "") -> None:
        if self.ui:
            self.ui.show_info("Plugin system loaded. Use --plugin-dir to load plugins.")

    def _cmd_agents(self, args: str = "") -> None:
        if self.ui:
            self.ui.show_info("No running agents.")

    def _cmd_review(self, args: str = "") -> None:
        if self.ui:
            self.ui.show_info(
                "Code review: use `git diff` to see changes, "
                "then ask Claude to review them."
            )

    def _cmd_init(self, args: str = "") -> None:
        if not self.ui:
            return
        import os
        from pathlib import Path
        claude_md = Path(self.config.working_directory) / "CLAUDE.md"
        if claude_md.exists():
            self.ui.show_info(f"CLAUDE.md already exists at {claude_md}")
            return
        project_name = Path(self.config.working_directory).name
        template = (
            f"# {project_name}\n\n"
            f"## Overview\nDescribe your project here.\n\n"
            f"## Tech Stack\n- \n\n"
            f"## Code Conventions\n- \n\n"
            f"## Testing\n```\n# test command\n```\n"
        )
        claude_md.write_text(template, encoding="utf-8")
        self.ui.show_info(f"Created CLAUDE.md at {claude_md}")

    def _cmd_add_dir(self, args: str = "") -> None:
        if not self.ui:
            return
        if not args.strip():
            self.ui.show_info("Usage: /add-dir <path>")
            return
        path = args.strip()
        if path not in self.config.allowed_directories:
            self.config.allowed_directories.append(path)
        self.ui.show_info(f"Added directory: {path}")

    def _cmd_rename(self, args: str = "") -> None:
        if not self.ui:
            return
        if not args.strip():
            self.ui.show_info("Usage: /rename <new-name>")
            return
        if self.session:
            self.session.metadata.name = args.strip()
            self.session_manager.save_session(self.session)
            self.ui.show_info(f"Session renamed to: {args.strip()}")
