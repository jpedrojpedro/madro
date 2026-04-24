import re
import unicodedata
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI

_STOPWORDS = frozenset(
    "a an the and or but in on at to for of with is are was were be been".split()
)
_CHUNK_SIZE = 512
_CHUNK_OVERLAP = 64


@dataclass
class NormalisedArtifact:
    canonical_text: str
    lexical_text: str       # normalised text — stored via to_tsvector at DB level
    chunks: list[str]
    embeddings: list[list[float]]
    provenance: dict


def _normalise_lexical(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = [t for t in text.split() if t not in _STOPWORDS]
    return " ".join(tokens)


def _chunk(text: str) -> list[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + _CHUNK_SIZE]))
        i += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks or [text]


async def _embed(chunks: list[str], client: AsyncOpenAI) -> list[list[float]]:
    response = await client.embeddings.create(
        model="text-embedding-3-small",
        input=chunks,
    )
    return [item.embedding for item in response.data]


async def normalise(
    raw: str,
    source_uri: str,
    agent_name: str,
    openai_client: AsyncOpenAI,
) -> NormalisedArtifact:
    chunks = _chunk(raw)
    embeddings = await _embed(chunks, openai_client)

    return NormalisedArtifact(
        canonical_text=raw,
        lexical_text=_normalise_lexical(raw),
        chunks=chunks,
        embeddings=embeddings,
        provenance={"source": source_uri, "agent": agent_name},
    )
