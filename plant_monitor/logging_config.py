from __future__ import annotations

import logging

from rich.console import Console
from rich.highlighter import RegexHighlighter
from rich.logging import RichHandler
from rich.theme import Theme


class PlantLogHighlighter(RegexHighlighter):
    base_style = "plant."
    highlights = [
        r"(?P<dryrun>DRY_RUN)",
        r"(?P<service>call_service|notify\.[a-zA-Z0-9_]+|switch\.[a-zA-Z0-9_]+)",
        r"(?P<entity>(?:plant|sensor|switch|binary_sensor)\.[a-zA-Z0-9_]+)",
        r"(?P<status>\bgreen\b|\borange\b|\bred\b)",
    ]


def setup_logging(level: str) -> None:
    theme = Theme(
        {
            "plant.dryrun": "bold yellow",
            "plant.service": "cyan",
            "plant.entity": "bright_blue",
            "plant.status": "bold",
        }
    )
    logging.basicConfig(
        level=level.upper(),
        format="%(name)s: %(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                show_time=True,
                show_level=True,
                show_path=False,
                rich_tracebacks=True,
                highlighter=PlantLogHighlighter(),
                console=Console(theme=theme),
            )
        ],
        force=True,
    )
    logging.getLogger("rich").handlers.clear()
