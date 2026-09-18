import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from django.conf import settings

logger = logging.getLogger(__name__)


class ModelConfig(BaseModel):
    name: str = "gemini-3.5-flash"
    temperature: float = 0.0
    max_tokens: int = 65_535


class DebugConfig(BaseModel):
    enabled: bool = False
    log_level: str = "INFO"


class FusionConfig(BaseModel):
    alpha: float = 0.7
    beta: float = 0.3


class AppConfig(BaseModel):
    model: ModelConfig = ModelConfig()
    image_model: ModelConfig = ModelConfig()
    qwen_baseline_model: ModelConfig = ModelConfig(name="qwen2.5-coder:7b")
    llama_baseline_model: ModelConfig = ModelConfig(name="llama3.1:8b")
    retrieval_sql_model: ModelConfig = ModelConfig(name="a-kore/Arctic-Text2SQL-R1-7B")
    # Which resolver class every RetrievalAgent uses for its on-the-fly SQL
    # generation — "gemini" or "arctic" — see retrieval_agents/sql_resolvers.py.
    retrieval_sql_backend: str = "gemini"
    fusion: FusionConfig = FusionConfig()
    debug: DebugConfig = DebugConfig()


# TODO: fetch on application initialization
def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        path = Path(settings.BASE_DIR) / "configs" / "default.yaml"
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return AppConfig.model_validate(raw)


def get_model() -> GoogleModel:
    cfg = load_config()
    provider = GoogleProvider(api_key=os.environ["GOOGLE_API_KEY"])
    return GoogleModel(cfg.model.name, provider=provider)


OLLAMA_BASE_URL = "http://localhost:11434/v1"


def get_image_model() -> OpenAIChatModel:
    """EnrichmentAgent's vision model (captioning/OCR), served locally via Ollama."""
    cfg = load_config()
    provider = OpenAIProvider(base_url=OLLAMA_BASE_URL, api_key="ollama")
    return OpenAIChatModel(cfg.image_model.name, provider=provider)


def get_qwen_coder_model() -> OpenAIChatModel:
    """Qwen2.5-Coder served locally via Ollama — a second naive-SQL baseline
    alongside Gemini, run entirely on-machine so it has no rate limit or API
    cost. Ollama's OpenAI-compatible endpoint ignores the api_key value; a
    placeholder is passed only because the underlying AsyncOpenAI client
    requires a non-empty string."""
    cfg = load_config()
    provider = OpenAIProvider(base_url=OLLAMA_BASE_URL, api_key="ollama")
    return OpenAIChatModel(cfg.qwen_baseline_model.name, provider=provider)


def get_llama_model() -> OpenAIChatModel:
    """Llama 3.1 8B served locally via Ollama — a third naive-SQL baseline: same
    size class and zero-cost/no-rate-limit setup as get_qwen_coder_model(), but
    a generalist model rather than a code-specialized one."""
    cfg = load_config()
    provider = OpenAIProvider(base_url=OLLAMA_BASE_URL, api_key="ollama")
    return OpenAIChatModel(cfg.llama_baseline_model.name, provider=provider)


def get_retrieval_sql_model() -> OpenAIChatModel:
    """Arctic-Text2SQL-R1-7B served locally via Ollama — used by every
    RetrievalAgent to write its own scoped SQL on the fly (see
    retrieval_agents/schema_scope.py), instead of Gemini: no rate limit/quota,
    no dependency on Gemini's availability, and trained specifically for
    text-to-SQL rather than incidentally capable of it."""
    cfg = load_config()
    provider = OpenAIProvider(base_url=OLLAMA_BASE_URL, api_key="ollama")
    return OpenAIChatModel(cfg.retrieval_sql_model.name, provider=provider)


# Paid tier: 4,000 requests/minute — was 4.0s (free tier's 15 req/min) before
# billing was enabled. A tiny non-zero floor still protects against a literal
# simultaneous burst (retrieval jobs can run concurrently, all sharing this
# same throttle), without meaningfully pacing anything at this quota.
GEMINI_MIN_INTERVAL_SECONDS = 60 / 4000
GEMINI_MAX_RETRIES = 5
GEMINI_RETRY_BACKOFF_SECONDS = 5.0
# 429 (rate limit) and 503 ("high demand", per Gemini's own error message —
# a transient outage, not a request problem) are both worth retrying;
# anything else (4xx like a bad request) is a real error, not transient.
GEMINI_RETRYABLE_STATUS_CODES = {429, 503}

_rate_limit_lock = asyncio.Lock()
_last_call_at: float | None = None


async def _throttle() -> None:
    """Blocks until at least GEMINI_MIN_INTERVAL_SECONDS have passed since the last call."""
    global _last_call_at
    async with _rate_limit_lock:
        now = time.monotonic()
        if _last_call_at is not None:
            wait = GEMINI_MIN_INTERVAL_SECONDS - (now - _last_call_at)
            if wait > 0:
                await asyncio.sleep(wait)
        _last_call_at = time.monotonic()


async def run_agent(agent: Agent, prompt: str) -> Any:
    """
    Runs a pydantic_ai Agent against Gemini with rate-limit throttling and
    429/503 retry — use this instead of calling agent.run() directly wherever
    get_model() is used, so callers don't each need their own backoff logic.
    """
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        await _throttle()
        try:
            return await agent.run(prompt)
        except ModelHTTPError as exc:
            if exc.status_code not in GEMINI_RETRYABLE_STATUS_CODES or attempt == GEMINI_MAX_RETRIES:
                raise
            backoff = GEMINI_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Gemini error (status %d, attempt %d/%d), retrying in %.0fs",
                exc.status_code, attempt, GEMINI_MAX_RETRIES, backoff,
            )
            await asyncio.sleep(backoff)
