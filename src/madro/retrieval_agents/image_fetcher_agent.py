from madro.config import get_retrieval_sql_model
from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef
from madro.retrieval_agents.schema_scope import build_scoped_schema
from madro.sql_generation import NaiveSQLBaseline, plain_runner

SCOPED_TABLES = ["raw_file", "publication", "profile"]


class ImageFetcherAgent(RetrievalAgent):
    identity = EntityRef(field="publication_id", kind="publication")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") or 25
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        username = kwargs.get("username")

        prompt = self._with_username_hint(demand, username)
        if date_from:
            prompt += f"\n\nOnly include images from publications published on or after {date_from}."
        if date_to:
            prompt += f"\n\nOnly include images from publications published on or before {date_to}."
        # The scoped schema's raw_file.data note ("never useful for a text
        # answer; don't select this") is correct for a text-answering agent —
        # wrong here, since this agent's whole job is fetching that data for
        # EnrichmentAgent's downstream OCR/captioning pass.
        prompt += (
            "\n\nUnlike a text-answering query, DO select raw_file.data (the "
            "image bytes) and raw_file.extension — filtered to extension = "
            "'jpg' only — since this feeds an image captioning/OCR pipeline, "
            "not a text answer."
        )

        resolver = NaiveSQLBaseline(
            model=get_retrieval_sql_model(),
            runner=plain_runner,
            use_native_output=True,
            schema_doc=build_scoped_schema(SCOPED_TABLES),
            result_limit=sample,
        )
        outcome = await resolver.resolve(prompt, identity_hint=self.identity.field)
        return [self._decode_bytes_values(row) for row in (outcome.rows or [])]

    @staticmethod
    def _decode_bytes_values(row: dict) -> dict:
        """raw_file.data comes back from psycopg as bytes/memoryview holding
        base64 ASCII text (dowser stores images pre-base64-encoded) —
        RetrievalOut's base64 detection only recognizes str values, so it
        must be decoded before this row is returned. Checked by value, not a
        fixed column name — the on-the-fly query is free to alias this
        column however it likes (observed aliasing it as `image_bytes`, not
        `data`, in practice)."""
        return {
            key: (bytes(val).decode() if isinstance(val, (bytes, memoryview)) else val)
            for key, val in row.items()
        }
