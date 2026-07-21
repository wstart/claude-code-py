"""AskUser tool — structured questions for the user.

Displays one or more structured questions with optional choices and
collects answers.  Supports multi-select and free-text responses.

The actual user interaction is delegated to a callback stored in the
ToolContext metadata (``ask_user_callback``).  If no callback is
configured, the tool returns the questions for the caller to handle.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from claude_code.tools.base import Tool, ToolResult

# Type for the user-interaction callback
# Receives the questions list, returns a dict mapping question index -> answer
AskUserCallback = Callable[
    [list[dict[str, Any]]],
    Coroutine[Any, Any, dict[str, Any]],
]

# Metadata key for the callback
ASK_USER_CALLBACK_KEY = "ask_user_callback"

# Maximum number of questions per call
_MAX_QUESTIONS = 4

# Maximum options per question
_MAX_OPTIONS = 10


class AskUserTool(Tool):
    """Display structured questions and collect user answers."""

    name = "AskUser"
    description = (
        "Displays structured questions to the user with optional multiple-"
        "choice answers. Supports 1-4 questions at once. Each question can "
        "have 2-10 options with labels and descriptions. Supports multi-select."
    )
    category = "interaction"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "List of question objects.",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question text.",
                        },
                        "header": {
                            "type": "string",
                            "description": "Short header/label for the question.",
                        },
                        "options": {
                            "type": "array",
                            "description": "List of answer options.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "description": "Option label.",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "Option description.",
                                    },
                                },
                                "required": ["label"],
                            },
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "description": "Allow selecting multiple options. Defaults to false.",
                        },
                    },
                    "required": ["question"],
                },
                "minItems": 1,
                "maxItems": 4,
            },
        },
        "required": ["questions"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        questions: list[dict[str, Any]] = kwargs["questions"]

        if not isinstance(questions, list) or not questions:
            return ToolResult.error("'questions' must be a non-empty list")

        if len(questions) > _MAX_QUESTIONS:
            return ToolResult.error(
                f"Maximum {_MAX_QUESTIONS} questions per call, got {len(questions)}"
            )

        # Validate each question
        errors: list[str] = []
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                errors.append(f"questions[{i}] must be an object")
                continue

            if not q.get("question"):
                errors.append(f"questions[{i}] missing 'question' text")

            options = q.get("options", [])
            if not isinstance(options, list):
                errors.append(f"questions[{i}] 'options' must be a list")
            elif len(options) > _MAX_OPTIONS:
                errors.append(
                    f"questions[{i}] has {len(options)} options "
                    f"(max {_MAX_OPTIONS})"
                )

        if errors:
            return ToolResult.error(
                "Validation errors:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        # Try to get answers via callback
        callback: AskUserCallback | None = self.context.metadata.get(
            ASK_USER_CALLBACK_KEY
        )

        if callback is not None:
            try:
                answers = await callback(questions)
                return ToolResult.success(_format_answers(questions, answers))
            except Exception as exc:
                return ToolResult.error(f"Failed to collect user input: {exc}")

        # No callback: format questions for display and return them
        # The caller (query engine) is responsible for rendering and collecting
        return ToolResult.success(_format_questions(questions))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _format_questions(questions: list[dict[str, Any]]) -> str:
    """Format questions for display when no callback is available."""
    lines: list[str] = ["Questions for the user:\n"]

    for qi, q in enumerate(questions):
        header = q.get("header", f"Question {qi + 1}")
        text = q["question"]
        multi = q.get("multiSelect", False)

        lines.append(f"### {header}")
        lines.append(text)

        if multi:
            lines.append("(Select multiple)")

        options = q.get("options", [])
        if options:
            for oi, opt in enumerate(options):
                label = opt.get("label", f"Option {oi + 1}")
                desc = opt.get("description", "")
                if desc:
                    lines.append(f"  {oi + 1}. **{label}** — {desc}")
                else:
                    lines.append(f"  {oi + 1}. **{label}**")

        lines.append("")

    return "\n".join(lines)


def _format_answers(
    questions: list[dict[str, Any]],
    answers: dict[str, Any],
) -> str:
    """Format collected answers into a readable summary."""
    lines: list[str] = ["User responses:\n"]

    for qi, q in enumerate(questions):
        key = str(qi)
        text = q.get("question", f"Question {qi + 1}")
        answer = answers.get(key, answers.get(qi, "No answer"))

        lines.append(f"  Q: {text}")

        if isinstance(answer, list):
            lines.append(f"  A: {', '.join(str(a) for a in answer)}")
        else:
            lines.append(f"  A: {answer}")
        lines.append("")

    return "\n".join(lines)
