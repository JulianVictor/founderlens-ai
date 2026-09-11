"""Verification of Supabase Auth access tokens.

Supabase issues a signed JWT on sign-in. This module turns one of those tokens
into an :class:`AuthenticatedUser`, or refuses.

**Verification is local.** The token's signature is checked against the project
JWT secret rather than by calling Supabase's ``/auth/v1/user`` endpoint. That
keeps authentication off the network path -- every authenticated request would
otherwise pay a round trip and inherit Supabase's availability -- and a JWT is
designed to be verified by its audience. The cost is that a token stays valid
until it expires even if the session was revoked server-side, which is why
Supabase issues short-lived access tokens and why we do not lengthen them.

**Nothing in an unverified token is trusted.** Claims are read only after the
signature, expiry and audience have been checked. In particular the user id is
taken from ``sub`` on a verified token and never from a header, a query
parameter or a request body.

HS256 is the algorithm Supabase uses for project JWT secrets. The permitted
algorithm list is explicit and closed: accepting whatever the token's header
asks for is the classic JWT vulnerability, letting an attacker present
``alg: none`` or downgrade an asymmetric key to a symmetric one. Migrating to
asymmetric signing keys (JWKS) means adding a verifier here; no caller changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import jwt

from app.core.config import Settings
from app.core.exceptions import AuthenticationError

#: The only signature algorithm we will accept. Never widen this to include an
#: asymmetric algorithm alongside a symmetric one -- that combination is what
#: enables the public-key-as-HMAC-secret confusion attack.
_ALLOWED_ALGORITHMS = ["HS256"]


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """A caller whose access token verified.

    This is the identity the rest of the application trusts. It is only ever
    constructed from a token that passed every check in
    :meth:`SupabaseJWTVerifier.verify`.
    """

    id: UUID
    email: str | None
    #: Supabase's own role claim ("authenticated", "anon", "service_role").
    #: This is *not* a workspace role: it says how the caller reached the API,
    #: not what they may do to any particular workspace. Workspace authority
    #: comes from the workspace_members table and nowhere else.
    auth_role: str


class SupabaseJWTVerifier:
    """Verifies Supabase access tokens against the project JWT secret."""

    def __init__(
        self,
        *,
        secret: str,
        audience: str,
        leeway_seconds: int = 0,
    ) -> None:
        if not secret:
            # Failing here rather than at verification time turns a
            # misconfigured deployment into a startup error instead of a
            # service that accepts nothing and says "invalid token".
            raise ValueError(
                "SUPABASE_JWT_SECRET is not configured; the API cannot verify access tokens"
            )
        self._secret = secret
        self._audience = audience
        self._leeway_seconds = leeway_seconds

    def verify(self, token: str) -> AuthenticatedUser:
        """Verify ``token`` and return the user it identifies.

        Raises:
            AuthenticationError: if the token is malformed, unsigned, signed
                with the wrong key or algorithm, expired, issued for a
                different audience, or missing a usable subject.
        """
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=_ALLOWED_ALGORITHMS,
                audience=self._audience,
                leeway=self._leeway_seconds,
                options={
                    # An access token without an expiry is not one we will
                    # accept: it would be valid forever.
                    "require": ["exp", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                },
            )
        except jwt.PyJWTError as exc:
            # Every failure mode collapses to the same error and the same
            # message. The specific reason goes to logs, not to the caller.
            raise AuthenticationError from exc

        subject = claims.get("sub")
        if not isinstance(subject, str):
            raise AuthenticationError

        try:
            user_id = UUID(subject)
        except ValueError as exc:
            # Supabase subjects are UUIDs. Anything else is not a token we
            # issued, whatever it was signed with.
            raise AuthenticationError from exc

        email = claims.get("email")
        auth_role = claims.get("role")

        return AuthenticatedUser(
            id=user_id,
            email=email if isinstance(email, str) and email else None,
            auth_role=auth_role if isinstance(auth_role, str) else "authenticated",
        )


def build_verifier(settings: Settings) -> SupabaseJWTVerifier:
    """Construct the verifier described by ``settings``."""
    return SupabaseJWTVerifier(
        secret=settings.supabase_jwt_secret.get_secret_value(),
        audience=settings.supabase_jwt_audience,
        leeway_seconds=settings.supabase_jwt_leeway_seconds,
    )
