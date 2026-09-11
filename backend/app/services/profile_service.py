"""Profile reads for the authenticated caller."""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import ProfileNotFoundError
from app.models.profile import Profile
from app.repositories.profile_repository import ProfileRepository


class ProfileService:
    """Reads the profile belonging to an authenticated user."""

    def __init__(self, repository: ProfileRepository) -> None:
        self._repository = repository

    async def get_for_user(self, user_id: UUID) -> Profile:
        """The caller's own profile.

        Raises:
            ProfileNotFoundError: the user has no profile row. The signup
                trigger creates one in the same transaction as the account, so
                this means the trigger is missing -- a deployment fault worth
                reporting as a server error rather than as an empty response.
        """
        profile = await self._repository.get(user_id)
        if profile is None:
            raise ProfileNotFoundError(user_id)
        return profile
