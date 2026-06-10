from typing import Protocol

from app.schemas import NormalizedItem


class SourceAdapter(Protocol):
    async def collect(self) -> list[NormalizedItem]:
        """Return newly discovered normalized content."""
        ...

