"""Theme system for terminal UI.

Defines color schemes and styles for all UI elements, supporting
both dark and light terminal backgrounds.
"""

from dataclasses import dataclass, field
from typing import Optional

from rich.style import Style
from rich.theme import Theme


@dataclass(frozen=True)
class ColorScheme:
    """Color definitions for UI elements."""

    # Primary colors
    primary: str = "bright_cyan"
    secondary: str = "bright_magenta"
    accent: str = "bright_green"

    # Semantic colors
    success: str = "green"
    warning: str = "yellow"
    error: str = "red"
    info: str = "cyan"

    # Tool category colors
    tool_file: str = "blue"
    tool_shell: str = "green"
    tool_web: str = "cyan"
    tool_search: str = "magenta"
    tool_default: str = "yellow"

    # Message colors
    user_message: str = "bright_white"
    assistant_message: str = "white"
    system_message: str = "dim"

    # UI element colors
    border: str = "dim"
    spinner: str = "bright_cyan"
    status_bar_bg: str = "grey23"
    status_bar_fg: str = "white"
    placeholder: str = "dim"


# Predefined schemes
DARK_SCHEME = ColorScheme()

LIGHT_SCHEME = ColorScheme(
    primary="blue",
    secondary="magenta",
    accent="green",
    user_message="black",
    assistant_message="grey15",
    system_message="grey50",
    border="grey70",
    status_bar_bg="grey85",
    status_bar_fg="black",
    placeholder="grey50",
)


@dataclass(frozen=True)
class ThemeConfig:
    """Complete theme configuration."""

    name: str
    scheme: ColorScheme
    code_theme: str  # Pygments theme name
    rich_theme: Theme = field(default_factory=lambda: Theme())

    def get_tool_style(self, tool_name: str) -> str:
        """Get the color for a given tool name.

        Args:
            tool_name: Name of the tool.

        Returns:
            Color string for the tool category.
        """
        name_lower = tool_name.lower()
        if any(k in name_lower for k in ("read", "write", "edit", "file", "glob")):
            return self.scheme.tool_file
        if any(k in name_lower for k in ("bash", "shell", "command", "exec")):
            return self.scheme.tool_shell
        if any(k in name_lower for k in ("web", "fetch", "http", "search", "browse")):
            return self.scheme.tool_web
        if any(k in name_lower for k in ("grep", "find", "search", "list")):
            return self.scheme.tool_search
        return self.scheme.tool_default

    def build_styles(self) -> dict[str, Style]:
        """Build a mapping of style names to rich Style objects.

        Returns:
            Dictionary of style name to Style.
        """
        s = self.scheme
        return {
            "primary": Style(color=s.primary, bold=True),
            "secondary": Style(color=s.secondary),
            "accent": Style(color=s.accent),
            "success": Style(color=s.success),
            "warning": Style(color=s.warning),
            "error": Style(color=s.error, bold=True),
            "info": Style(color=s.info),
            "user": Style(color=s.user_message, bold=True),
            "assistant": Style(color=s.assistant_message),
            "system": Style(color=s.system_message, italic=True),
            "border": Style(color=s.border),
            "spinner": Style(color=s.spinner, bold=True),
            "placeholder": Style(color=s.placeholder, italic=True),
            "status_bar": Style(color=s.status_bar_fg, bgcolor=s.status_bar_bg),
        }


# Predefined themes
DARK_THEME = ThemeConfig(
    name="dark",
    scheme=DARK_SCHEME,
    code_theme="monokai",
    rich_theme=Theme(
        {
            "markdown.h1": Style(bold=True, color="bright_white"),
            "markdown.h2": Style(bold=True, color="bright_cyan"),
            "markdown.h3": Style(bold=True, color="bright_green"),
            "markdown.code": Style(color="bright_yellow"),
            "markdown.code_block": Style(color="bright_yellow"),
        }
    ),
)

LIGHT_THEME = ThemeConfig(
    name="light",
    scheme=LIGHT_SCHEME,
    code_theme="default",
    rich_theme=Theme(
        {
            "markdown.h1": Style(bold=True, color="black"),
            "markdown.h2": Style(bold=True, color="blue"),
            "markdown.h3": Style(bold=True, color="green"),
            "markdown.code": Style(color="magenta"),
            "markdown.code_block": Style(color="magenta"),
        }
    ),
)


def get_theme(name: Optional[str] = None) -> ThemeConfig:
    """Get a theme by name.

    Args:
        name: Theme name ('dark' or 'light'). Defaults to 'dark'.

    Returns:
        ThemeConfig instance.
    """
    themes = {"dark": DARK_THEME, "light": LIGHT_THEME}
    return themes.get(name or "dark", DARK_THEME)
