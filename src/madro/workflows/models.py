from uuid import UUID
from pydantic import BaseModel


class NewTopic(BaseModel):
    name: str
    description: str


class TopicAssignment(BaseModel):
    topic_ids: list[UUID]
    new_topic: "NewTopic | None" = None
