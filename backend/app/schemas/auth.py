"""API contracts for the authenticated caller."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class MeResponse(BaseModel):
    """Identity and profile of the caller, returned by ``GET /api/v1/me``."""

    id: UUID
    # Plain str: this is an outbound projection of a value Supabase Auth
    # already validated, so re-validating it here would only add a dependency.
    email: str | None
    display_name: str
    created_at: datetime
