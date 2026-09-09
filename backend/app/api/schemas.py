"""Shared response schemas for persisted run events."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class RunEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    sequence: int
    event_type: str
    payload: dict
