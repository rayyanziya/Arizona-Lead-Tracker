"""Admin CRUD schemas: keywords and monitored sources."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.models import Language, MatchType, Platform
from app.services.facebook_group import facebook_group_id


def _require_nonblank(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


# --- Keywords ---------------------------------------------------------------
class KeywordCreate(BaseModel):
    term: str
    language: Language = Language.ANY
    match_type: MatchType = MatchType.PHRASE
    is_active: bool = True

    _term = field_validator("term")(_require_nonblank)


class KeywordUpdate(BaseModel):
    term: str | None = None
    language: Language | None = None
    match_type: MatchType | None = None
    is_active: bool | None = None

    @field_validator("term")
    @classmethod
    def _term_nonblank(cls, value: str | None) -> str | None:
        return _require_nonblank(value) if value is not None else None


class KeywordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    term: str
    language: str
    match_type: str
    is_active: bool
    created_at: datetime


# --- Monitored sources ------------------------------------------------------
class SourceCreate(BaseModel):
    platform: Platform
    identifier: str
    label: str | None = None
    is_active: bool = True

    _identifier = field_validator("identifier")(_require_nonblank)

    @model_validator(mode="after")
    def _facebook_identifier_is_a_group(self) -> SourceCreate:
        # Facebook can only be monitored per-group, so a Facebook source must
        # resolve to a group (URL or bare id). Rejecting here means the operator
        # gets a 422 at add-time instead of a source that silently scrapes nothing.
        if self.platform is Platform.FACEBOOK and facebook_group_id(self.identifier) is None:
            raise ValueError(
                "Facebook source must be a group URL or id, e.g. "
                "https://facebook.com/groups/<id> or just <id>"
            )
        return self


class SourceUpdate(BaseModel):
    identifier: str | None = None
    label: str | None = None
    is_active: bool | None = None

    @field_validator("identifier")
    @classmethod
    def _identifier_nonblank(cls, value: str | None) -> str | None:
        return _require_nonblank(value) if value is not None else None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    identifier: str
    label: str | None = None
    is_active: bool
    last_scraped_at: datetime | None = None
    created_at: datetime