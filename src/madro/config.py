import os
from pathlib import Path
import yaml
from pydantic import BaseModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from django.conf import settings


class ModelConfig(BaseModel):
    name: str = "gemini-3.5-flash"
    temperature: float = 0.0
    max_tokens: int = 65_535


class DebugConfig(BaseModel):
    enabled: bool = False
    log_level: str = "INFO"


class TextFusionConfig(BaseModel):
    alpha: float = 0.7
    beta: float = 0.3


class ImageFusionConfig(BaseModel):
    gamma: float = 0.5
    delta: float = 0.5


class ModalityWeightsConfig(BaseModel):
    w_t: float = 0.7
    w_i: float = 0.3


class FusionConfig(BaseModel):
    text: TextFusionConfig = TextFusionConfig()
    image: ImageFusionConfig = ImageFusionConfig()
    modality_weights: ModalityWeightsConfig = ModalityWeightsConfig()


class AppConfig(BaseModel):
    model: ModelConfig = ModelConfig()
    image_model: ModelConfig = ModelConfig()
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


def get_image_model_name() -> str:
    cfg = load_config()
    return cfg.image_model.name
