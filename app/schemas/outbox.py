from datetime import datetime

from pydantic import BaseModel, ConfigDict


class OutboxEventResponse(BaseModel):
    id: int
    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    payload_json: str
    status: str
    retry_count: int
    max_retries: int
    last_error: str | None = None
    created_at: datetime
    processed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class OutboxRelayResponse(BaseModel):
    status: str
    processed_count: int
    failed_count: int
    total_scanned: int
    message: str
