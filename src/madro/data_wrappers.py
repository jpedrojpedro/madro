from uuid import UUID
import base64
from typing import Any
from dataclasses import dataclass
from pydantic import BaseModel, Field, model_validator, field_validator


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
    image_content: list[str] = Field(default_factory=list)
    text_content: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def _is_base64(s: str) -> bool:
        if not isinstance(s, str):
            return False
        try:
            base64.b64decode(s.encode('utf-8'), validate=True)
            return True
        except Exception:
            return False

    @model_validator(mode="before")
    @classmethod
    def normalize_incoming_response(cls, data: Any) -> Any:
        # If it's already an instance or a pre-shaped dictionary matching the fields,
        # pass it through
        if isinstance(data, dict) and (
            "image_content" in data or "text_content" in data
        ):
            return data

        # 1. Handle raw String inputs
        if isinstance(data, str):
            if cls._is_base64(data):
                return {"image_content": [data]}
            return {"text_content": [{"content": data}]}

        # 2. Handle flat Key-Value Dictionaries
        if isinstance(data, dict):
            image_content = []
            text_content = []
            for key, val in data.items():
                if isinstance(val, str) and cls._is_base64(val):
                    image_content.append(val)
                else:
                    text_content.append({key: val})
            return {"image_content": image_content, "text_content": text_content}

        # 3. Handle Lists of Dictionaries
        if isinstance(data, list):
            if not data or not isinstance(data[0], dict):
                raise ValueError("List input must contain dictionary items.")

            image_content = []
            text_content = []
            for dict_elem in data:
                txt_payload = {}
                for key, val in dict_elem.items():
                    if isinstance(val, str) and cls._is_base64(val):
                        image_content.append(val)
                    else:
                        txt_payload[key] = val
                if txt_payload:
                    text_content.append(txt_payload)

            return {"image_content": image_content, "text_content": text_content}

        raise ValueError(f"Unsupported data type for parsing: {type(data)}")

    @field_validator("image_content", mode="after")
    @classmethod
    def validate_base64_images(cls, v: list[str]) -> list[str]:
        # Keeps your original explicit validation check alive during field assignment
        for index, item in enumerate(v):
            if not cls._is_base64(item):
                raise ValueError(
                    f"Invalid base64 string found at image_content[{index}]")
        return v


@dataclass
class LexicalIndex:
    normalization: str


@dataclass
class SemanticIndex:
    chunks: list[str]
    embeddings: list[list[float]]
    model: str


@dataclass
class NormalisedArtifact:
    canonical_text: str  # Now stores the synthesized Markdown text
    lexical_index: LexicalIndex
    semantic_index: SemanticIndex
    provenance: dict
