"""Routes that exist only to exercise the workspace dependencies.

Phase 1 ships no workspace-scoped endpoint, but the dependencies that future
routes will rely on are the security-critical part of the phase. These probes
let that contract be tested now rather than whenever the first real route lands.

Defined at module scope on purpose: FastAPI resolves a route's string
annotations against the enclosing *module's* globals, so a dependency alias
declared inside a function body cannot be resolved and silently degrades into a
required query parameter.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import WorkspaceMember, require_workspace_role
from app.models.workspace import WorkspaceMembership, WorkspaceRole

OwnerOnly = Annotated[WorkspaceMembership, Depends(require_workspace_role(WorkspaceRole.OWNER))]

router = APIRouter(prefix="/probe/workspaces", tags=["probe"])


@router.get("/{workspace_id}")
async def any_member(membership: WorkspaceMember) -> dict[str, str]:
    """Requires membership in any role."""
    return {"workspace": membership.workspace.name, "role": membership.role.value}


@router.get("/{workspace_id}/owner-only")
async def owner_only(membership: OwnerOnly) -> dict[str, str]:
    """Requires the owner role."""
    return {"workspace": membership.workspace.name, "role": membership.role.value}
