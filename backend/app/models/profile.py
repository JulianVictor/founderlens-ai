"""Persistence shape for a user profile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Profile:
    """A row of ``public.profiles``."""

    id: UUID
    display_name: str
    created_at: datetime
