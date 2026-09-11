"""The authenticated caller's own identity."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, ProfileServiceDep
from app.schemas.auth import MeResponse

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=MeResponse, summary="Identity of the calling user")
async def read_me(user: CurrentUser, profiles: ProfileServiceDep) -> MeResponse:
    """Return the caller's identity and profile.

    The id and email come from the verified token; the display name comes from
    the profile row. The token is the authority on *who* the caller is -- the
    database is never consulted to establish identity, only to decorate it.
    """
    profile = await profiles.get_for_user(user.id)
    return MeResponse(
        id=user.id,
        email=user.email,
        display_name=profile.display_name,
        created_at=profile.created_at,
    )
