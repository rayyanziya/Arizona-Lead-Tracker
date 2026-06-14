"""Lead (matched + scored post) schemas for the dashboard API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import MatchStatus


class LeadPostOut(BaseModel):
    """The originating post shown alongside a lead."""

    model_config = ConfigDict(from_attributes=True)

    platform: str
    external_id: str
    url: str
    author: str | None = None
    title: str | None = None
    body: str
    posted_at: datetime | None = None


class LeadOut(BaseModel):
    """A lead: the AI-scored match plus its source post."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    ai_score: int | None = None
    ai_is_buyer: bool | None = None
    ai_reason: str | None = None
    matched_term: str | None = None
    matched_terms: list | None = None
    created_at: datetime
    post: LeadPostOut


class LeadListOut(BaseModel):
    """A page of leads plus the unpaginated total for the current filters."""

    items: list[LeadOut]
    total: int
    limit: int
    offset: int


class LeadStatusUpdate(BaseModel):
    """Triage a lead. The enum makes an unknown status a 422 at the boundary."""

    status: MatchStatus