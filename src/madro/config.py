from pathlib import Path
import yaml
from pydantic import BaseModel


class ModelConfig(BaseModel):
    provider: str = "openai"
    name: str = "gpt-4o"
    temperature: float = 0.2
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
