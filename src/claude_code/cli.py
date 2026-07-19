"""CLI entry point using Click."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Optional

import click

from claude_code import __version__


@click.group(invoke_without_command=True)
@click.argument("query", required=False)
@click.option(
    "--print", "-p",
    "print_mode",
    is_flag=True,
    default=False,
    help="Non-interactive mode: print response and exit.",
)
@click.option(
    "--continue", "-c",
    "continue_session",
    is_flag=True,
    default=False,
    help="Resume the most recent session.",
)
@click.option(
    "--resume", "-r",
    "resume_session",
    type=str,
    default=None,
    help="Resume a specific session by ID.",
)
@click.option(
    "--model",
    type=str,
    default=None,
    help="Override the model (e.g. claude-sonnet-4-20250514).",
)
@click.option(
    "--permission-mode",
    type=click.Choice(["manual", "auto", "plan", "bypass"]),
    default=None,
    help="Permission mode: manual (default), auto, plan, bypass.",
)
@click.option(
    "--output-format",
    type=click.Choice(["text", "json", "stream-json"]),
    default=None,
    help="Output format: text (default), json, stream-json.",
)
@click.option("--verbose", is_flag=True, default=False, help="Enable verbose/debug output.")
@click.option(
    "--max-turns",
    type=int,
    default=None,
    help="Maximum number of conversation turns (0 = unlimited).",
)
@click.option(
    "--max-budget-usd",
    type=float,
    default=None,
    help="Maximum budget in USD (0 = unlimited).",
)
@click.option(
    "--system-prompt",
    type=str,
    default=None,
    help="Override the system prompt.",
)
@click.option(
    "--add-dir",
    type=str,
    multiple=True,
    help="Add additional directories to the workspace.",
)
@click.option(
    "--dangerously-skip-permissions",
    is_flag=True,
    default=False,
    help="Skip all permission prompts (DANGEROUS).",
)
@click.option(
    "--effort",
    type=click.Choice(["low", "medium", "high"]),
    default=None,
    help="Reasoning effort level.",
)
@click.version_option(version=__version__, prog_name="claude")
@click.pass_context
def main(
    ctx: click.Context,
    query: Optional[str],
    print_mode: bool,
    continue_session: bool,
    resume_session: Optional[str],
    model: Optional[str],
    permission_mode: Optional[str],
    output_format: Optional[str],
    verbose: bool,
    max_turns: Optional[int],
    max_budget_usd: Optional[float],
    system_prompt: Optional[str],
    add_dir: tuple[str, ...],
    dangerously_skip_permissions: bool,
    effort: Optional[str],
) -> None:
    """Claude Code — an agentic coding assistant in your terminal.

    Start an interactive session, or pass a QUERY for non-interactive use.
    """
    ctx.ensure_object(dict)

    # Build overrides from CLI flags
    overrides: dict = {}
    if model:
        overrides["model"] = model
    if permission_mode:
        overrides["permission_mode"] = permission_mode
    if output_format:
        overrides["output_format"] = output_format
    if verbose:
        overrides["verbose"] = True
    if max_turns is not None:
        overrides["max_turns"] = max_turns
    if max_budget_usd is not None:
        overrides["max_budget_usd"] = max_budget_usd
    if system_prompt:
        overrides["system_prompt"] = system_prompt
    if add_dir:
        overrides["additional_dirs"] = list(add_dir)
    if dangerously_skip_permissions:
        overrides["dangerously_skip_permissions"] = True
    if effort:
        overrides["effort"] = effort

    # If a subcommand was invoked, do nothing here
    if ctx.invoked_subcommand is not None:
        return

    # Run the async application
    asyncio.run(_run_app(
        query=query,
        print_mode=print_mode,
        continue_session=continue_session,
        resume_session=resume_session,
        overrides=overrides,
    ))


async def _run_app(
    query: Optional[str],
    print_mode: bool,
    continue_session: bool,
    resume_session: Optional[str],
    overrides: dict,
) -> None:
    """Async entry point that initializes and runs the application.

    Args:
        query: Optional user query for non-interactive mode.
        print_mode: If True, output response and exit.
        continue_session: Resume most recent session.
        resume_session: Session ID to resume.
        overrides: CLI flag overrides.
    """
    from claude_code.core.config import load_config
    from claude_code.utils.logging import setup_logging

    # Load configuration
    config = load_config(overrides=overrides)

    # Setup logging
    log_level = "DEBUG" if config.verbose else "INFO"
    setup_logging(level=log_level)

    if print_mode or query:
        await _run_print_mode(query, config)
    else:
        await _run_interactive_mode(config, continue_session, resume_session)


async def _run_print_mode(query: Optional[str], config) -> None:
    """Non-interactive mode: process query and print result.

    Args:
        query: The user's query text.
        config: Application configuration.
    """
    if not query:
        # Read from stdin if no query provided
        if not sys.stdin.isatty():
            query = sys.stdin.read().strip()
        else:
            click.echo("Error: No query provided.", err=True)
            sys.exit(1)

    output_format = config.output_format

    # Placeholder: will be replaced when the engine is implemented
    response = f"[Engine not yet implemented] Query: {query}"

    if output_format == "json":
        result = {"type": "result", "result": response, "session_id": ""}
        click.echo(json.dumps(result, indent=2))
    elif output_format == "stream-json":
        # Emit as a single stream event
        event = {"type": "assistant", "message": {"content": [{"type": "text", "text": response}]}}
        click.echo(json.dumps(event))
    else:
        click.echo(response)


async def _run_interactive_mode(
    config,
    continue_session: bool,
    resume_session: Optional[str],
) -> None:
    """Interactive mode: REPL with rich UI.

    Args:
        config: Application configuration.
        continue_session: Resume most recent session.
        resume_session: Session ID to resume.
    """
    # Placeholder: will be replaced when UI and engine are implemented
    click.echo(f"Claude Code v{__version__} (Python)")
    click.echo(f"Model: {config.model}")
    click.echo(f"Working directory: {config.working_directory}")
    if continue_session:
        click.echo("Resuming most recent session...")
    if resume_session:
        click.echo(f"Resuming session: {resume_session}")
    click.echo()
    click.echo("Interactive mode not yet implemented. Use --print for non-interactive mode.")


# --- Subcommands ---


@main.command()
@click.argument("action", type=click.Choice(["login", "logout", "status"]))
def auth(action: str) -> None:
    """Manage authentication (login, logout, status)."""
    if action == "login":
        click.echo("Opening browser for authentication...")
        click.echo("(Auth flow not yet implemented)")
    elif action == "logout":
        click.echo("Logged out successfully.")
    elif action == "status":
        # Check if API key is available
        import os
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if has_key:
            click.echo("Authenticated (API key found in environment)")
        else:
            click.echo("Not authenticated. Run `claude auth login` to authenticate.")


@main.command()
def doctor() -> None:
    """Run diagnostics and check system health."""
    import os
    import shutil

    click.echo("Claude Code Doctor")
    click.echo("=" * 40)

    # Python version
    click.echo(f"Python: {sys.version.split()[0]}")

    # Package version
    click.echo(f"Claude Code: v{__version__}")

    # Check git
    git_path = shutil.which("git")
    click.echo(f"Git: {git_path or 'NOT FOUND'}")

    # Check API key
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    click.echo(f"API Key: {'configured' if has_key else 'NOT SET'}")

    # Check settings
    settings_path = os.path.expanduser("~/.claude/settings.json")
    has_settings = os.path.exists(settings_path)
    click.echo(f"User settings: {'found' if has_settings else 'not found'}")

    # Check CLAUDE.md
    claude_md = os.path.expanduser("~/.claude/CLAUDE.md")
    has_claude_md = os.path.exists(claude_md)
    click.echo(f"Global CLAUDE.md: {'found' if has_claude_md else 'not found'}")

    click.echo()
    if git_path and has_key:
        click.echo("✓ System looks healthy!")
    else:
        click.echo("⚠ Some checks failed. See above for details.")


@main.command()
def update() -> None:
    """Check for and install updates."""
    click.echo(f"Current version: {__version__}")
    click.echo("Checking for updates...")
    click.echo("(Update mechanism not yet implemented)")


if __name__ == "__main__":
    main()
