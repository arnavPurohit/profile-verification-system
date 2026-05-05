from __future__ import annotations

from typing import Protocol

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..domain import Profile


class ProfilesRepoProtocol(Protocol):
    async def get(self, urn: str) -> Profile | None: ...
    async def get_by_url_or_slug(self, value: str) -> Profile | None: ...
    async def upsert(self, profile: Profile) -> None: ...


class ProfilesRepo:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._col = db.profiles

    async def get(self, urn: str) -> Profile | None:
        doc = await self._col.find_one({"urn": urn})
        if not doc:
            return None
        doc.pop("_id", None)
        return Profile.model_validate(doc)

    async def get_by_url_or_slug(self, value: str) -> Profile | None:
        doc = await self._col.find_one({"$or": [{"url": value}, {"public_id": value}]})
        if not doc:
            return None
        doc.pop("_id", None)
        return Profile.model_validate(doc)

    async def upsert(self, profile: Profile) -> None:
        await self._col.update_one(
            {"urn": profile.urn},
            {"$set": profile.model_dump(mode="json")},
            upsert=True,
        )
