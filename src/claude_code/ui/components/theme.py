"""Theme — Cyberpunk Security Terminal aesthetic.

High-contrast, precision-engineered interface for offensive
security operations. Deep blacks, neon accents, tight spacing.
"""

from dataclasses import dataclass, field
from typing import Optional

from rich.style import Style
from rich.theme import Theme


@dataclass(frozen=True)
class ColorScheme:
    """Color definitions — cyberpunk security palette."""

    # Identity
    aka_red: str = "#ff3e3e"          # AKA brand — blood/neon red
    user_cyan: str = "#00e5ff"         # User input — electric cyan

    # Accents
    accent_green: str = "#39ff14"      # Success / shell — toxic green
    accent_purple: str = "#c084fc"     # Web / MCP — neon purple
    accent_amber: str = "#ffb700"      # Warning / search — amber

    # Semantic
    success: str = "#39ff14"
    warning: str = "#ffb700"
    error: str = "#ff3e3e"
    info: str = "#00e5ff"

    # Tool categories
    tool_file: str = "#7dd3fc"         # Ice blue
    tool_shell: str = "#39ff14"        # Toxic green
    tool_web: str = "#c084fc"          # Neon purple
    tool_search: str = "#ffb700"       # Amber
    tool_default: str = "#94a3b8"      # Slate

    # Text hierarchy
    text_primary: str = "#f1f5f9"      # Near-white
    text_secondary: str = "#94a3b8"    # Slate
    text_dim: str = "#475569"          # Dark slate
    text_code: str = "#fde68a"         # Warm yellow for inline code

    # UI chrome
    border: str = "#1e293b"            # Very dark blue-grey
    border_active: str = "#ff3e3e"     # Red when active
    bg_subtle: str = "#0f172a"         # Deep navy
    bg_panel: str = "#020617"          # Near-black
    bg_input: str = "#0f172a"          # Input area background

    # Special
    spinner: str = "#ff3e3e"
    placeholder: str = "#475569"
    cursor: str = "#ff3e3e"
    status_fg: str = "#64748b"
    status_bg: str = "#020617"
    separator: str = "#1e293b"


DARK_SCHEME = ColorScheme()

LIGHT_SCHEME = ColorScheme(
    aka_red="#dc2626",
    user_cyan="#0891b2",
    accent_green="#16a34a",
    accent_purple="#9333ea",
    accent_amber="#d97706",
    text_primary="#0f172a",
    text_secondary="#475569",
    text_dim="#94a3b8",
    text_code="#b45309",
    border="#e2e8f0",
    border_active="#dc2626",
    bg_subtle="#f8fafc",
    bg_panel="#ffffff",
    bg_input="#f1f5f9",
    spinner="#dc2626",
    placeholder="#94a3b8",
    cursor="#dc2626",
    status_fg="#64748b",
    status_bg="#f8fafc",
    separator="#e2e8f0",
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
        if any(k in n for k in ("bash", "shell", "exec", "kill", "diagnostics")):
            return self.scheme.tool_shell
        if any(k in n for k in ("web", "fetch", "http", "search")):
            return self.scheme.tool_web
        if any(k in n for k in ("grep", "glob", "find", "ls")):
            return self.scheme.tool_search
        return self.scheme.tool_default

    def get_tool_icon(self, tool_name: str) -> str:
        """Get icon for a tool by category."""
        n = tool_name.lower()
        if any(k in n for k in ("read",)):
            return "📖"
        if any(k in n for k in ("write",)):
            return "📝"
        if any(k in n for k in ("edit", "multi")):
            return "✏️"
        if any(k in n for k in ("bash", "shell", "exec")):
            return "⚡"
        if any(k in n for k in ("grep", "search")):
            return "🔍"
        if any(k in n for k in ("glob", "find")):
            return "📂"
        if any(k in n for k in ("web", "fetch")):
            return "🌐"
        if any(k in n for k in ("notebook",)):
            return "📓"
        if any(k in n for k in ("task",)):
            return "🤖"
        if any(k in n for k in ("todo",)):
            return "✅"
        if any(k in n for k in ("ls",)):
            return "📋"
        if any(k in n for k in ("kill",)):
            return "💀"
        return "⚙️"


def _make_rich_theme(s: ColorScheme) -> Theme:
    return Theme({
        "markdown.h1": Style(bold=True, color=s.aka_red),
        "markdown.h2": Style(bold=True, color=s.aka_red),
        "markdown.h3": Style(bold=True, color=s.user_cyan),
        "markdown.h4": Style(bold=True, color=s.accent_purple),
        "markdown.code": Style(color=s.text_code, bgcolor=s.bg_subtle),
        "markdown.code_block": Style(color=s.text_primary, bgcolor=s.bg_subtle),
        "markdown.link": Style(color=s.user_cyan, underline=True),
        "markdown.em": Style(italic=True, color=s.text_secondary),
        "markdown.strong": Style(bold=True, color=s.text_primary),
        "markdown.blockquote": Style(color=s.text_secondary, italic=True),
        "markdown.hr": Style(color=s.separator),
        "markdown.item.bullet": Style(color=s.aka_red, bold=True),
        "markdown.item.number": Style(color=s.aka_red, bold=True),
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
