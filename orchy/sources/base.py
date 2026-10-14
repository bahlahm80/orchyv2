from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class JobPosting:
    external_id: str
    title: str
    company: str
    location: str
    url: str
    description: str
    source: str = "linkedin"
    raw: dict = field(default_factory=dict)


class JobSource(Protocol):
    name: str

    async def search(self, keywords: str, location: str) -> list[JobPosting]:
        ...
