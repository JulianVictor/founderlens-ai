"""Schemas for the health endpoint."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Liveness payload returned by ``GET /health``."""

    status: Literal["ok"]
    service: str


class GraphHealthResponse(BaseModel):
    """Reachability of the graph database.

    Separate from liveness because they answer different questions: ``/health``
    says the process is up, this says a dependency it needs is usable. A load
    balancer wants the first; an operator diagnosing failed ingestion wants the
    second.
    """

    status: Literal["ok", "unavailable"]
    service: Literal["neo4j"] = "neo4j"
