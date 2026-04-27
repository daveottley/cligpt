from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable

try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text
    from rich.theme import Theme
except ModuleNotFoundError:  # pragma: no cover - exercised by plain fallback users
    Console = None
    Live = None
    Markdown = None
    Panel = None
    Syntax = None
    Text = None
    Theme = None


THEME = (
    Theme(
        {
            "role.user": "bold cyan",
            "role.assistant": "bold green",
            "role.system": "bold magenta",
            "role.tool": "bold yellow",
            "meta": "dim",
            "error": "bold red",
            "ok": "bold green",
            "warn": "bold yellow",
        }
    )
    if Theme
    else None
)


@dataclass
class RenderConfig:
    width: int | None = None
    style: str = "auto"
    no_color: bool = False
    code_theme: str = "monokai"


def resolve_style(style: str | None) -> str:
    requested = (style or os.getenv("CLIGPT_STYLE") or "auto").lower()
    if requested == "auto":
        return "codex" if sys.stdout.isatty() else "plain"
    if requested in {"codex", "compact", "plain"}:
        return requested
    return "plain"


class TerminalRenderer:
    def __init__(self, config: RenderConfig | None = None) -> None:
        self.config = config or RenderConfig()
        self.style = resolve_style(self.config.style)
        self.rich_available = Console is not None
        self.enabled = self.style != "plain" and self.rich_available
        self.console = (
            Console(
                theme=THEME,
                width=self.config.width,
                no_color=self.config.no_color or os.getenv("NO_COLOR") is not None,
                force_terminal=sys.stdout.isatty(),
            )
            if self.rich_available
            else None
        )

    def meta(self, text: str) -> None:
        if self.enabled:
            self.console.print(Text(text, style="meta"))
        else:
            print(text)

    def user(self, text: str) -> None:
        if self.enabled:
            self._panel("user", Text(text), "cyan")
        else:
            print(text)

    def assistant(self, markdown_text: str) -> None:
        if self.enabled:
            self._panel("assistant", self._markdown(markdown_text), "green")
        else:
            print(markdown_text)

    def error(self, text: str) -> None:
        if self.enabled:
            self._panel("error", Text(text), "red")
        else:
            print(text, file=sys.stderr)

    def tool(self, title: str, body: str, language: str = "bash") -> None:
        if self.enabled:
            syntax = Syntax(body, language, theme=self.config.code_theme, word_wrap=True)
            self._panel(title, syntax, "yellow")
        else:
            print(f"{title}:\n{body}")

    def diff(self, diff_text: str) -> None:
        self.tool("diff", diff_text, "diff")

    def render_stream(self, chunks: Iterable[str]) -> str:
        if not self.enabled:
            text = []
            for chunk in chunks:
                text.append(chunk)
                print(chunk, end="", flush=True)
            return "".join(text)

        buffer: list[str] = []
        with Live(
            self._assistant_panel(""),
            console=self.console,
            refresh_per_second=8,
            transient=False,
        ) as live:
            for chunk in chunks:
                buffer.append(chunk)
                live.update(self._assistant_panel("".join(buffer)))
        return "".join(buffer)

    def _markdown(self, text: str):
        return Markdown(text, code_theme=self.config.code_theme)

    def _assistant_panel(self, text: str):
        return Panel(
            self._markdown(text or "*waiting for model output...*"),
            title="[role.assistant]assistant[/]",
            border_style="green",
            padding=(0, 1),
        )

    def _panel(self, title: str, body, border: str) -> None:
        if self.style == "compact":
            self.console.print(Text(f"\n{title}", style=f"role.{self._role_for_title(title)}"))
            self.console.print(body)
            return
        self.console.print(
            Panel(
                body,
                title=f"[role.{self._role_for_title(title)}]{title}[/]",
                border_style=border,
                padding=(0, 1),
            )
        )

    @staticmethod
    def _role_for_title(title: str) -> str:
        lowered = title.lower()
        if "user" in lowered:
            return "user"
        if "assistant" in lowered:
            return "assistant"
        if "error" in lowered:
            return "error"
        if "tool" in lowered or "command" in lowered or "diff" in lowered:
            return "tool"
        return "system"
