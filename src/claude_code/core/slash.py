"""Slash command dispatcher — extracted from app.py."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_code.core.app import ClaudeApp


class SlashDispatcher:
    """Handles all /command input."""

    def __init__(self, app: ClaudeApp) -> None:
        self.app = app

    async def handle(self, command: str) -> bool:
        """Handle a slash command. Returns True if handled."""
        parts = command.split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handler = self._commands.get(cmd)
        if handler:
            handler(args)
            return True
        return False

    @property
    def _commands(self) -> dict:
        return {
            "/help": self.cmd_help,
            "/?": self.cmd_help,
            "/clear": self.cmd_clear,
            "/compact": self.cmd_compact,
            "/cost": self.cmd_cost,
            "/usage": self.cmd_cost,
            "/model": self.cmd_model,
            "/status": self.cmd_status,
            "/config": self.cmd_config,
            "/settings": self.cmd_config,
            "/memory": self.cmd_memory,
            "/doctor": self.cmd_doctor,
            "/resume": self.cmd_resume,
            "/bug": self.cmd_bug,
            "/permissions": self.cmd_permissions,
            "/perms": self.cmd_permissions,
            "/hooks": self.cmd_hooks,
            "/mcp": self.cmd_mcp,
            "/plugins": self.cmd_plugins,
            "/agents": self.cmd_agents,
            "/review": self.cmd_review,
            "/init": self.cmd_init,
            "/add-dir": self.cmd_add_dir,
            "/rename": self.cmd_rename,
            "/loop": self.cmd_loop,
            "/exit": self.cmd_exit,
            "/quit": self.cmd_exit,
        }

    # ------------------------------------------------------------------
    # Individual command handlers
    # ------------------------------------------------------------------

    def cmd_help(self, args: str = "") -> None:
        if not self.app.ui:
            return
        self.app.ui.show_info(
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
            "  /loop          Recurring task (natural language)\n"
            "  /bug           Report a bug\n"
            "  /exit          Exit Claude Code\n\n"
            "  Enter=submit  Esc+Enter=newline  Ctrl+C=cancel"
        )

    def cmd_clear(self, args: str = "") -> None:
        if self.app.query_engine:
            self.app.query_engine.reset()
        if self.app.ui:
            self.app.ui.show_info("Conversation cleared.")

    def cmd_compact(self, args: str = "") -> None:
        if not self.app.query_engine or not self.app.ui:
            return
        from claude_code.core.context import compress

        api_msgs = self.app.query_engine.conversation.to_api_messages()
        compressed = compress(api_msgs, max_tokens=self.app.config.max_tokens or 200_000)
        self.app.query_engine._rebuild_conversation(compressed)
        self.app.ui.show_info(f"Compacted: {len(api_msgs)} → {len(compressed)} messages")

    def cmd_cost(self, args: str = "") -> None:
        if self.app.query_engine and self.app.ui:
            c = self.app.query_engine.get_cost_info()
            self.app.ui.show_info(
                f"Tokens — Input: {c.input_tokens:,}, Output: {c.output_tokens:,}, "
                f"Total: {c.total_tokens:,}"
            )

    def cmd_model(self, args: str = "") -> None:
        if not self.app.ui:
            return
        if args:
            self.app.config.model = args
            self.app.ui.show_info(f"Model → {args}")
            self.app.ui.update_status(model=args)
        else:
            self.app.ui.show_info(f"Current model: {self.app.config.model}")

    def cmd_status(self, args: str = "") -> None:
        if not self.app.ui:
            return
        cost = self.app.query_engine.get_cost_info() if self.app.query_engine else None
        info = (
            f"Session: {self.app.session.metadata.id if self.app.session else 'N/A'}\n"
            f"Model: {self.app.config.model}\n"
            f"Working dir: {self.app.config.working_directory}\n"
            f"Permission: {self.app.config.permission_mode}"
        )
        if cost:
            info += f"\nTokens: {cost.total_tokens:,}"
        self.app.ui.show_info(info)

    def cmd_config(self, args: str = "") -> None:
        if self.app.ui:
            self.app.ui.show_info(
                f"Provider: {self.app.config.provider}\n"
                f"Model: {self.app.config.model}\n"
                f"Permission: {self.app.config.permission_mode}\n"
                f"Working dir: {self.app.config.working_directory}\n"
                f"Max tokens: {self.app.config.max_tokens}\n"
                f"Max turns: {self.app.config.max_turns}"
            )

    def cmd_memory(self, args: str = "") -> None:
        if not self.app.ui:
            return
        md = self.app.config.claude_md_content
        self.app.ui.show_info(f"CLAUDE.md:\n\n{md}" if md else "No CLAUDE.md found.")

    def cmd_doctor(self, args: str = "") -> None:
        if not self.app.ui:
            return
        checks: list[str] = []
        checks.append(f"✓ Python {sys.version.split()[0]}")

        if os.environ.get("ANTHROPIC_API_KEY"):
            checks.append("✓ ANTHROPIC_API_KEY is set")
        else:
            checks.append("✗ ANTHROPIC_API_KEY is not set")

        if self.app.provider:
            checks.append(f"✓ Provider: {self.app.provider.provider_name}")

        if self.app.tool_registry:
            names = self.app.tool_registry.list_names()
            checks.append(f"✓ Tools: {len(names)} ({', '.join(names)})")

        wd = Path(self.app.config.working_directory)
        checks.append(f"{'✓' if wd.exists() else '✗'} Working dir: {wd}")

        try:
            from claude_code.utils.git_utils import is_git_repo

            if is_git_repo(self.app.config.working_directory):
                checks.append("✓ Git repository")
        except Exception:
            pass

        try:
            from claude_code.utils.platform import has_command

            checks.append(f"{'✓' if has_command('rg') else '○'} ripgrep")
        except Exception:
            pass

        self.app.ui.show_info("Diagnostics:\n\n" + "\n".join(checks))

    def cmd_exit(self, args: str = "") -> None:
        self.app._running = False

    def cmd_resume(self, args: str = "") -> None:
        if not self.app.ui or not self.app.query_engine:
            return
        session_id = args.strip() if args.strip() else None
        if session_id:
            loaded = self.app.session_manager.load_session(session_id)
        else:
            loaded = self.app.session_manager.get_latest_session()
        if loaded is None:
            self.app.ui.show_info("No previous session found.")
            return
        self.app.session = loaded
        from claude_code.core.message import Conversation, Message

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
                self.app.ui.display_user_message(text)
            elif role == "assistant":
                self.app.ui.display_assistant_message(text)
        self.app.query_engine.conversation = conv
        self.app.ui.show_info(
            f"Resumed session {loaded.metadata.id[:8]}... "
            f"({len(loaded.messages)} messages)"
        )

    def cmd_bug(self, args: str = "") -> None:
        if self.app.ui:
            self.app.ui.show_info(
                "Report bugs at: https://github.com/anthropics/claude-code/issues"
            )

    def cmd_permissions(self, args: str = "") -> None:
        if not self.app.ui:
            return
        mode = self.app.config.permission_mode
        info = f"Permission mode: {mode}\n\n"
        if self.app.permission_manager:
            try:
                rules = self.app.permission_manager._rule_engine
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
        self.app.ui.show_info(info)

    def cmd_hooks(self, args: str = "") -> None:
        if not self.app.ui:
            return
        if self.app.hook_manager:
            try:
                hooks = self.app.hook_manager.list_hooks()
                if hooks:
                    lines = []
                    for event, entries in hooks.items():
                        for e in entries:
                            lines.append(f"  {event}: {e.get('command', '?')}")
                    self.app.ui.show_info("Active hooks:\n" + "\n".join(lines))
                else:
                    self.app.ui.show_info("No hooks configured.")
            except Exception:
                self.app.ui.show_info("No hooks configured.")
        else:
            self.app.ui.show_info("Hook manager not initialized.")

    def cmd_mcp(self, args: str = "") -> None:
        if not self.app.ui:
            return
        servers = self.app.config.mcp_servers if hasattr(self.app.config, "mcp_servers") else {}
        if servers:
            lines = [f"  {name}: {cfg}" for name, cfg in servers.items()]
            self.app.ui.show_info("MCP servers:\n" + "\n".join(lines))
        else:
            self.app.ui.show_info("No MCP servers configured.")

    def cmd_plugins(self, args: str = "") -> None:
        if self.app.ui:
            self.app.ui.show_info("Plugin system loaded. Use --plugin-dir to load plugins.")

    def cmd_agents(self, args: str = "") -> None:
        if self.app.ui:
            self.app.ui.show_info("No running agents.")

    def cmd_review(self, args: str = "") -> None:
        if self.app.ui:
            self.app.ui.show_info(
                "Code review: use `git diff` to see changes, "
                "then ask Claude to review them."
            )

    def cmd_init(self, args: str = "") -> None:
        if not self.app.ui:
            return
        claude_md = Path(self.app.config.working_directory) / "CLAUDE.md"
        if claude_md.exists():
            self.app.ui.show_info(f"CLAUDE.md already exists at {claude_md}")
            return
        project_name = Path(self.app.config.working_directory).name
        template = (
            f"# {project_name}\n\n"
            f"## Overview\nDescribe your project here.\n\n"
            f"## Tech Stack\n- \n\n"
            f"## Code Conventions\n- \n\n"
            f"## Testing\n```\n# test command\n```\n"
        )
        claude_md.write_text(template, encoding="utf-8")
        self.app.ui.show_info(f"Created CLAUDE.md at {claude_md}")

    def cmd_add_dir(self, args: str = "") -> None:
        if not self.app.ui:
            return
        if not args.strip():
            self.app.ui.show_info("Usage: /add-dir <path>")
            return
        path = args.strip()
        if path not in self.app.config.allowed_directories:
            self.app.config.allowed_directories.append(path)
        self.app.ui.show_info(f"Added directory: {path}")

    def cmd_rename(self, args: str = "") -> None:
        if not self.app.ui:
            return
        if not args.strip():
            self.app.ui.show_info("Usage: /rename <new-name>")
            return
        if self.app.session:
            self.app.session.metadata.name = args.strip()
            self.app.session_manager.save_session(self.app.session)
            self.app.ui.show_info(f"Session renamed to: {args.strip()}")

    def cmd_loop(self, args: str = "") -> None:
        """Run a task described in natural language, repeatedly.

        Usage: /loop <describe what to do>
        The AI decides the pacing based on the task description.

        To stop: /loop stop or Ctrl+C
        """
        if not self.app.ui or not self.app.query_engine:
            return

        if not args.strip():
            self.app.ui.show_info(
                "Usage: /loop <describe your task>\n\n"
                "  /loop 监控服务器状态，有变化就通知我\n"
                "  /loop 每隔几分钟检查CI是否跑完\n"
                "  /loop 持续扫描新子域名并记录结果\n\n"
                "AI 自动决定循环节奏。Ctrl+C 或 /loop stop 停止。"
            )
            return

        # Stop
        if args.strip().lower() in ("stop", "cancel", "kill"):
            self.app._loop_active = False
            self.app.ui.show_info("Loop stopped.")
            return

        # Start loop in foreground
        self.app._loop_active = True
        self.app._loop_desc = args.strip()
        self.app.ui.show_info(f"Loop started: {self.app._loop_desc} (Ctrl+C to stop)")
