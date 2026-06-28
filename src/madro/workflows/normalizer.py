import re
import unicodedata
import asyncio
from functools import partial
from pathlib import Path
from django.conf import settings
from madro.data_wrappers import RetrievalOut, LexicalIndex, SemanticIndex, NormalisedArtifact
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

    def synthesize_markdown(self, records: RetrievalOut) -> str:
        """
        Assembles the three core streams (OCR, Description, and Structured data)
        into a single, highly readable Markdown structure.
        """
        markdown_lines = []

        # Tier 1: Contextual Visual Description (VLM output)
        # Assuming your updated RetrievalOut contains this field or parsed images
        if hasattr(records, "image_description") and records.image_description:
            markdown_lines.extend(
                ["## Visual Description", records.image_description, ""])
        elif records.image_content:
            markdown_lines.extend(["## Visual Description",
                                   f"[Contains {len(records.image_content)} Image Payload(s)]",
                                   ""])

        # Tier 2: Extracted Text (Sparse/Dense OCR Content)
        if records.text_content:
            markdown_lines.append("## Extracted Text (OCR)")
            for item in records.text_content:
                if isinstance(item, dict):
                    for k, v in item.items():
                        markdown_lines.append(f"**{k}**: {v}")
                else:
                    markdown_lines.append(str(item))
            markdown_lines.append("")

        # Tier 3: Relational / Tabular Data (Serialized Dictionary Payload)
        # Using a fallback to convert structural dictionaries into neat markdown lines
        if records.text_content and any(
                isinstance(i, dict) and len(i) > 1 for i in records.text_content):
            markdown_lines.append("## Tabular and Structured Data")
            for item in records.text_content:
                if isinstance(item, dict) and len(item) > 1:
                    markdown_lines.append("| Property | Value |")
                    markdown_lines.append("| :--- | :--- |")
                    for k, v in item.items():
                        markdown_lines.append(f"| {k} | {v} |")
            markdown_lines.append("")

        return "\n".join(markdown_lines).strip()

    def _normalise_lexical(self, text: str) -> str:
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

    async def normalise(self, records: RetrievalOut) -> NormalisedArtifact:
        """
        Main execution pipeline. Converts raw multimodal records into unified markdown,
        then processes both index pipelines concurrently.
        """
        # 1. Generate the centralized 3-Tier Canonical Structure
        canonical_markdown = self.synthesize_markdown(records)

        # 2. Extract elements for Lexical and Semantic Indices
        chunks = self._chunk(canonical_markdown)
        embeddings = await self._embed(chunks)
        lexical_normalization = self._normalise_lexical(canonical_markdown)

        return NormalisedArtifact(
            canonical_text=canonical_markdown,
            lexical_index=LexicalIndex(normalization=lexical_normalization),
            semantic_index=SemanticIndex(
                chunks=chunks,
                embeddings=embeddings,
                model=self.embedding_model
            ),
            provenance=records.provenance or {},
        )
