from uuid import UUID
from pydantic import BaseModel, model_validator


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


class NewTopic(BaseModel):
    name: str
    description: str


class TopicAssignment(BaseModel):
    topic_ids: list[UUID] = []
    new_topic: NewTopic | None = None

    @model_validator(mode="after")
    def check_exclusive(self) -> "TopicAssignment":
        if not self.topic_ids and self.new_topic is None:
            raise ValueError("Must provide either topic_ids or new_topic")
        if self.topic_ids and self.new_topic is not None:
            raise ValueError("Provide topic_ids or new_topic, not both")
        return self


class EnrichedPrompt(BaseModel):
    rewritten_prompt: str


class SubDemand(BaseModel):
    demand: str
    topic_name: str


class DecomposedDemand(BaseModel):
    sub_demands: list[SubDemand]
