import re
import unicodedata
import asyncio
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from sentence_transformers import SentenceTransformer

_STOPWORDS = frozenset(
    "a an the and or but in on at to for of with is are was were be been".split()
)
_CHUNK_SIZE = 512
_CHUNK_OVERLAP = 64
_EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
_EMBEDDING_DIMS = 768
_CACHE_DIR = Path.home() / ".cache" / "madro" / "models"

_encoder: SentenceTransformer | None = None


def _get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _encoder = SentenceTransformer(
            _EMBEDDING_MODEL,
            cache_folder=str(_CACHE_DIR),
        )
    return _encoder


@dataclass
class LexicalIndex:
    normalization: str


@dataclass
class SemanticIndex:
    chunks: list[str]
    embeddings: list[list[float]]
    model: str = _EMBEDDING_MODEL


@dataclass
class NormalisedArtifact:
    canonical_text: str
    lexical_index: LexicalIndex
    semantic_index: SemanticIndex
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


async def _embed(chunks: list[str]) -> list[list[float]]:
    loop = asyncio.get_event_loop()
    encoder = _get_encoder()
    embeddings = await loop.run_in_executor(None, partial(encoder.encode, chunks, convert_to_numpy=False))
    return [e.tolist() for e in embeddings]


async def normalise(
    raw: str,
    provenance: dict,
) -> NormalisedArtifact:
    chunks = _chunk(raw)
    embeddings = await _embed(chunks)

    return NormalisedArtifact(
        canonical_text=raw,
        lexical_index=LexicalIndex(
            normalization=_normalise_lexical(raw),
        ),
        semantic_index=SemanticIndex(
            chunks=chunks,
            embeddings=embeddings,
        ),
        provenance=provenance,
    )
