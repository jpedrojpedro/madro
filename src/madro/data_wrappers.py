from uuid import UUID
from pydantic import BaseModel, field_validator, model_validator
import base64
from typing import Any


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


class RetrievalOut(BaseModel):
    image_content: list[str] = None
    text_content: list[dict[str, Any]] = None
    provenance: dict[str, str] = None

    @field_validator("image_content", mode="after")
    @classmethod
    def validate_base64_images(cls, v: list[str]) -> list[str]:
        for index, item in enumerate(v):
            try:
                base64.b64decode(item.encode('utf-8'), validate=True)
            except Exception as e:
                raise ValueError(
                    f"Invalid base64 string found at image_content[{index}]: {e}"
                )
        return v
