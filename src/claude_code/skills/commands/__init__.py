"""Built-in slash commands and the command base class."""

from claude_code.skills.commands.base import CommandContext, SlashCommand
from claude_code.skills.commands.add_dir import AddDirCommand
from claude_code.skills.commands.agents import AgentsCommand
from claude_code.skills.commands.bug import BugCommand
from claude_code.skills.commands.clear import ClearCommand
from claude_code.skills.commands.compact import CompactCommand
from claude_code.skills.commands.config import ConfigCommand
from claude_code.skills.commands.cost import CostCommand
from claude_code.skills.commands.doctor import DoctorCommand
from claude_code.skills.commands.help import HelpCommand
from claude_code.skills.commands.hooks import HooksCommand
from claude_code.skills.commands.init import InitCommand
from claude_code.skills.commands.loop import LoopCommand
from claude_code.skills.commands.mcp import McpCommand
from claude_code.skills.commands.memory import MemoryCommand
from claude_code.skills.commands.model import ModelCommand
from claude_code.skills.commands.permissions import PermissionsCommand
from claude_code.skills.commands.plugins import PluginsCommand
from claude_code.skills.commands.rename import RenameCommand
from claude_code.skills.commands.review import ReviewCommand
from claude_code.skills.commands.status import StatusCommand

# Registry mapping command names → instances for quick lookup
BUILTIN_COMMANDS: dict[str, SlashCommand] = {}


def _register_builtins() -> None:
    """Populate BUILTIN_COMMANDS from all imported command classes."""
    command_classes = [
        AddDirCommand,
        AgentsCommand,
        BugCommand,
        ClearCommand,
        CompactCommand,
        ConfigCommand,
        CostCommand,
        DoctorCommand,
        HelpCommand,
        HooksCommand,
        InitCommand,
        LoopCommand,
        McpCommand,
        MemoryCommand,
        ModelCommand,
        PermissionsCommand,
        PluginsCommand,
        RenameCommand,
        ReviewCommand,
        StatusCommand,
    ]
    for cls in command_classes:
        instance = cls()
        BUILTIN_COMMANDS[instance.name] = instance
        for alias in instance.aliases:
            BUILTIN_COMMANDS[alias] = instance


_register_builtins()

__all__ = [
    "AddDirCommand",
    "AgentsCommand",
    "BUILTIN_COMMANDS",
    "BugCommand",
    "ClearCommand",
    "CommandContext",
    "CompactCommand",
    "ConfigCommand",
    "CostCommand",
    "DoctorCommand",
    "HelpCommand",
    "HooksCommand",
    "InitCommand",
    "LoopCommand",
    "McpCommand",
    "MemoryCommand",
    "ModelCommand",
    "PermissionsCommand",
    "PluginsCommand",
    "RenameCommand",
    "ReviewCommand",
    "SlashCommand",
    "StatusCommand",
]
