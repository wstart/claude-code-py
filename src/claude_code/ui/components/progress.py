"""Progress indicator component for multi-step operations.

Displays task lists with checkmarks and optional progress bars
for operations with known completion percentages.
"""

from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme


@dataclass
class TaskItem:
    """A single task in a progress list."""

    description: str
    completed: bool = False
    error: Optional[str] = None


@dataclass
class TaskList:
    """A list of tasks for progress tracking."""

    title: str
    tasks: list[TaskItem] = field(default_factory=list)

    def add_task(self, description: str) -> int:
        """Add a task to the list.

        Args:
            description: Task description.

        Returns:
            Index of the added task.
        """
        self.tasks.append(TaskItem(description=description))
        return len(self.tasks) - 1

    def complete_task(self, index: int, error: Optional[str] = None) -> None:
        """Mark a task as completed.

        Args:
            index: Task index.
            error: Optional error message if the task failed.
        """
        if 0 <= index < len(self.tasks):
            self.tasks[index].completed = True
            self.tasks[index].error = error


class ProgressDisplay:
    """Displays progress for multi-step operations.

    Supports both checklist-style progress (task list with
    checkmarks) and percentage-based progress bars.

    Attributes:
        console: The rich Console for output.
        theme: The current theme configuration.
    """

    def __init__(
        self,
        console: Console,
        theme: Optional[ThemeConfig] = None,
    ) -> None:
        """Initialize progress display.

        Args:
            console: Rich Console instance.
            theme: Optional theme config.
        """
        self.console = console
        self.theme = theme or get_theme("dark")

    def render_task_list(self, task_list: TaskList) -> None:
        """Render a task list with checkmarks.

        Args:
            task_list: The TaskList to display.
        """
        table = Table(
            show_header=False,
            show_edge=False,
            show_lines=False,
            padding=(0, 1),
        )
        table.add_column("status", width=3)
        table.add_column("description")

        for task in task_list.tasks:
            if task.completed:
                if task.error:
                    status = Text("✗", style="bold red")
                    desc = Text(f"{task.description} — {task.error}", style="red")
                else:
                    status = Text("✓", style="bold green")
                    desc = Text(task.description, style="dim")
            else:
                status = Text("○", style="dim")
                desc = Text(task.description)

            table.add_row(status, desc)

        header = Text(f"\n{task_list.title}", style=f"bold {self.theme.scheme.primary}")
        self.console.print(header)
        self.console.print(table)

    def create_progress_bar(self, description: str = "Processing") -> Progress:
        """Create a rich progress bar for percentage-based tracking.

        Args:
            description: Description text for the progress bar.

        Returns:
            A configured rich.progress.Progress instance.
        """
        return Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            console=self.console,
        )
