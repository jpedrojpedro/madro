import os
from pathlib import Path
import yaml
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncAzureOpenAI


class ModelConfig(BaseModel):
    name: str = "gpt-4o"
    temperature: float = 0.0
    max_tokens: int = 4096


class DebugConfig(BaseModel):
    enabled: bool = False
    log_level: str = "INFO"


class AppConfig(BaseModel):
    model: ModelConfig = ModelConfig()
    debug: DebugConfig = DebugConfig()


def load_config(path: Path | None = None) -> AppConfig:
    if path is None:
        path = Path(__file__).resolve().parents[3] / "configs" / "default.yaml"
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    return AppConfig.model_validate(raw)


def get_model() -> OpenAIModel:
    cfg = load_config()
    client = AsyncAzureOpenAI(
        azure_endpoint=os.environ["AZURE_ENDPOINT"],
        api_version=os.environ.get("API_VERSION", "2025-04-01-preview"),
        api_key=os.environ["API_KEY"],
    )
    return OpenAIModel(
        cfg.model.name,
        provider=OpenAIProvider(openai_client=client),
    )
