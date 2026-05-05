from __future__ import annotations

from fastapi import APIRouter

from ..config import Settings


def build(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health():
        return {
            "ok": True,
            "mode": settings.fetcher_mode,
            "has_voyager_credentials": settings.has_voyager_credentials,
            "parser_version": settings.parser_version,
        }

    return router
