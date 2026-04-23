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


class ThreadIn(BaseModel):
    thread_id: UUID | None = None
    task_prompt: str


class SubDemandOut(BaseModel):
    demand: str
    topic_name: str


class ThreadOut(BaseModel):
    thread_id: UUID
    message_id: UUID
    sub_demands: list[SubDemandOut]
