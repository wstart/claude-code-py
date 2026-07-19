"""Theme system — Claude Code inspired terminal styling."""

from dataclasses import dataclass, field
from typing import Optional

from rich.style import Style
from rich.theme import Theme


@dataclass(frozen=True)
class ColorScheme:
    """Color definitions matching Claude Code's aesthetic."""

    # Brand colors
    claude_orange: str = "#d97757"
    user_blue: str = "#6cb6ff"
    accent: str = "#d97757"

    # Semantic
    success: str = "#7ee787"
    warning: str = "#d29922"
    error: str = "#f85149"
    info: str = "#6cb6ff"

    # Tool categories
    tool_file: str = "#79c0ff"
    tool_shell: str = "#7ee787"
    tool_web: str = "#d2a8ff"
    tool_search: str = "#ffa657"
    tool_default: str = "#d29922"

    # Text
    text_primary: str = "bright_white"
    text_secondary: str = "#8b949e"
    text_dim: str = "#484f58"

    # UI chrome
    border: str = "#30363d"
    border_active: str = "#d97757"
    bg_subtle: str = "#161b22"
    bg_panel: str = "#0d1117"
    spinner: str = "#d97757"
    placeholder: str = "#484f58"
    status_fg: str = "#8b949e"
    status_bg: str = "#010409"


DARK_SCHEME = ColorScheme()

LIGHT_SCHEME = ColorScheme(
    claude_orange="#bf5636",
    user_blue="#0969da",
    accent="#bf5636",
    text_primary="#1f2328",
    text_secondary="#656d76",
    text_dim="#8c959f",
    border="#d0d7de",
    border_active="#bf5636",
    bg_subtle="#f6f8fa",
    bg_panel="#ffffff",
    status_fg="#656d76",
    status_bg="#f6f8fa",
    placeholder="#8c959f",
    spinner="#bf5636",
)


@dataclass(frozen=True)
class ThemeConfig:
    """Complete theme configuration."""

    name: str
    scheme: ColorScheme
    code_theme: str
    rich_theme: Theme = field(default_factory=lambda: Theme())

    def get_tool_color(self, tool_name: str) -> str:
        """Get color for a tool by category."""
        n = tool_name.lower()
        if any(k in n for k in ("read", "write", "edit", "notebook", "multi")):
            return self.scheme.tool_file
        if any(k in n for k in ("bash", "shell", "exec", "kill")):
            return self.scheme.tool_shell
        if any(k in n for k in ("web", "fetch", "http", "search")):
            return self.scheme.tool_web
        if any(k in n for k in ("grep", "glob", "find", "ls")):
            return self.scheme.tool_search
        return self.scheme.tool_default


def _make_rich_theme(scheme: ColorScheme) -> Theme:
    return Theme({
        "markdown.h1": Style(bold=True, color=scheme.claude_orange),
        "markdown.h2": Style(bold=True, color=scheme.claude_orange),
        "markdown.h3": Style(bold=True, color=scheme.user_blue),
        "markdown.code": Style(color="#ffa657", bgcolor=scheme.bg_subtle),
        "markdown.code_block": Style(color="#e6edf3", bgcolor=scheme.bg_subtle),
        "markdown.link": Style(color=scheme.user_blue, underline=True),
        "markdown.em": Style(italic=True, color=scheme.text_secondary),
        "markdown.strong": Style(bold=True, color=scheme.text_primary),
        "markdown.blockquote": Style(color=scheme.text_secondary, italic=True),
    })


DARK_THEME = ThemeConfig(
    name="dark",
    scheme=DARK_SCHEME,
    code_theme="monokai",
    rich_theme=_make_rich_theme(DARK_SCHEME),
)

LIGHT_THEME = ThemeConfig(
    name="light",
    scheme=LIGHT_SCHEME,
    code_theme="default",
    rich_theme=_make_rich_theme(LIGHT_SCHEME),
)


def get_theme(name: Optional[str] = None) -> ThemeConfig:
    """Get a theme by name."""
    return {"dark": DARK_THEME, "light": LIGHT_THEME}.get(name or "dark", DARK_THEME)
