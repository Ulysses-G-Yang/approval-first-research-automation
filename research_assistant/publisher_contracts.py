from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DraftPackage:
    platform: str
    markdown_path: Path
    manifest_path: Path


class DraftPublisher(Protocol):
    """Reviewed plugins may implement this contract; built-ins never publish automatically."""

    platform: str

    async def save_draft(self, package: DraftPackage) -> str:
        """Return the platform draft URL only after the platform confirms a saved draft."""
        ...
