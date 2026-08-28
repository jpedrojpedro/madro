import re
import unicodedata
import asyncio
from functools import partial
from pathlib import Path
from django.conf import settings
from tabulate import tabulate
from madro.data_wrappers import RetrievalOut, LexicalIndex, SemanticIndex, NormalizedArtifact
from sentence_transformers import SentenceTransformer


class MultimodalNormalizer:
    def __init__(
            self,
            embedding_model: str = "nomic-ai/nomic-embed-text-v1.5",
            chunk_size: int = 512,
            chunk_overlap: int = 64,
            cache_dir: Path | None = None,
    ):
        self.embedding_model = embedding_model
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "madro" / "models")

        # Instance-bound encoder to eliminate global variable mutation
        self._encoder: SentenceTransformer | None = None

        self.stopwords_path = settings.BASE_DIR / "configs" / "stopwords.txt"
        self.stopwords = self._load_stopwords()

    def _load_stopwords(self) -> frozenset[str]:
        if self.stopwords_path.is_file():
            return frozenset(self.stopwords_path.read_text(encoding="utf-8").split())

        return frozenset(
            "a an the and or but in on at to for of with is are was were be been".split()
        )

    def _get_encoder(self) -> SentenceTransformer:
        """Lazily instantiates and caches the transformer within the class instance context."""
        if self._encoder is None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._encoder = SentenceTransformer(
                self.embedding_model,
                cache_folder=str(self.cache_dir),
            )
        return self._encoder

    _LONG_FIELD_THRESHOLD = 100

    @classmethod
    def _is_long_value(cls, value) -> bool:
        return isinstance(value, str) and (
            "\n" in value or len(value) > cls._LONG_FIELD_THRESHOLD
        )

    @classmethod
    def _split_fields(cls, item: dict) -> tuple[dict, dict]:
        """Splits a record's fields into inline-safe scalars and prose-length values."""
        scalar_fields = {k: v for k, v in item.items() if not cls._is_long_value(v)}
        long_fields = {k: v for k, v in item.items() if cls._is_long_value(v)}
        return scalar_fields, long_fields

    @classmethod
    def record_to_text(cls, record) -> str:
        """Renders one record as prose — scalar fields inline, long free-text
        fields (e.g. img_caption, ocr_text) as their own blocks. Shared by
        synthesize_markdown's per-record document and RelevanceRanker's
        per-entity ranking embedding, so the two representations never drift
        apart — see docs/adr/0004-per-entity-live-embedding-for-s-sem.md."""
        if not isinstance(record, dict):
            return str(record)
        lines = []
        scalar_fields, long_fields = cls._split_fields(record)
        if scalar_fields:
            lines.append(" · ".join(f"**{k}**: {v}" for k, v in scalar_fields.items()))
        for k, v in long_fields.items():
            lines.append(f"**{k}**")
            lines.append(v)
        return "\n".join(lines)

    def synthesize_markdown(self, records: RetrievalOut) -> str:
        """
        Renders text_content as a single Markdown representation: a compact
        table when every field is scalar, otherwise one block per record with
        long free-text fields (e.g. img_caption, ocr_text) kept as prose —
        tabulate's pipe format breaks on embedded newlines, so long text
        cannot be dumped into table cells.
        """
        if not records.text_content:
            return ""

        has_long_fields = any(
            isinstance(item, dict) and self._split_fields(item)[1]
            for item in records.text_content
        )

        if not has_long_fields:
            return tabulate(records.text_content, headers="keys", tablefmt="pipe")

        markdown_lines = []
        if records.image_content:
            markdown_lines.append(f"_{len(records.image_content)} image(s) sampled_")
            markdown_lines.append("")

        for i, item in enumerate(records.text_content, start=1):
            markdown_lines.append(f"### #{i}")
            text = self.record_to_text(item)
            if text:
                markdown_lines.append(text)
            markdown_lines.append("")

        return "\n".join(markdown_lines).strip()

    def _normalize_lexical(self, text: str) -> str:
        """Strips accents, symbols, markdown tokens, and builds low-level clean token list."""
        text = text.lower()
        text = unicodedata.normalize("NFD", text)
        text = "".join(c for c in text if unicodedata.category(c) != "Mn")

        # Explicitly strips out common markdown layout characters to optimize lexemas
        text = re.sub(r"[#*|:_\-\[\]]", " ", text)
        text = re.sub(r"[^\w\s]", " ", text)

        tokens = [t for t in text.split() if t not in self.stopwords]
        return " ".join(tokens)

    def _chunk(self, text: str) -> list[str]:
        """Splits the continuous text stream based on explicit word boundary constraints."""
        words = text.split()
        chunks, i = [], 0
        while i < len(words):
            chunks.append(" ".join(words[i: i + self.chunk_size]))
            i += self.chunk_size - self.chunk_overlap
        return chunks or [text]

    async def _embed(self, chunks: list[str]) -> list[list[float]]:
        """Invokes the underlying transformer model asynchronously inside an executor thread."""
        loop = asyncio.get_running_loop()
        encoder = self._get_encoder()
        embeddings = await loop.run_in_executor(
            None,
            partial(encoder.encode, chunks, convert_to_numpy=False)
        )
        return [e.tolist() for e in embeddings]

    async def normalize(self, records: RetrievalOut) -> NormalizedArtifact:
        """
        Main execution pipeline. Converts raw multimodal records into unified markdown,
        then processes both index pipelines concurrently.
        """
        # 1. Generate the centralized 3-Tier Canonical Structure
        canonical_markdown = self.synthesize_markdown(records)

        # 2. Extract elements for Lexical and Semantic Indices
        chunks = self._chunk(canonical_markdown)
        embeddings = await self._embed(chunks)
        lexical_normalization = self._normalize_lexical(canonical_markdown)

        return NormalizedArtifact(
            canonical_text=canonical_markdown,
            lexical_index=LexicalIndex(normalization=lexical_normalization),
            semantic_index=SemanticIndex(
                chunks=chunks,
                embeddings=embeddings,
                model=self.embedding_model
            ),
            provenance=records.provenance or {},
            raw_records=records.text_content,
        )
