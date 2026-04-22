from uuid import UUID
from pydantic import BaseModel


class AgentIn(BaseModel):
    name: str
    description: str
    uri: str
    mcp_schema: dict
    candidate_topics: list[str] | None = None


class TopicOut(BaseModel):
    id: UUID
    name: str
    description: str


class AgentOut(BaseModel):
    id: UUID
    topics: list[TopicOut]
