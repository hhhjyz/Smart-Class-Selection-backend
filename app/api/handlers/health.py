"""Health-check endpoints for deployment and gateway probes."""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.common import Envelope

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
@router.get("/api/course-selection/v1/health")
async def health() -> Envelope[dict[str, str]]:
    return Envelope.ok({"status": "healthy", "service": "course-selection"})
