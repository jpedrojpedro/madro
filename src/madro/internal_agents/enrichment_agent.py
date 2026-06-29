import base64
import io
import asyncio
import logging
from pathlib import Path
from functools import partial
from PIL import Image
import torch

from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info
from django.conf import settings

from madro.data_wrappers import RetrievalOut
from madro.config import get_image_model_name
from madro.internal_agents.system_prompts import EnrichmentAgentSP

logger = logging.getLogger(__name__)


class EnrichmentAgent:
    def __init__(
            self,
            model_name: str = get_image_model_name(),
            max_image_side: int = 1024,
            max_new_tokens: int = 1024,
    ):
        self.model_name = model_name
        self.max_image_side = max_image_side
        self.max_new_tokens = max_new_tokens

        # Resolve cache directory relative to Django BASE_DIR
        self.cache_dir = Path(settings.BASE_DIR) / ".cache" / "madro" / "models"

        # Instance-bound model and processor properties
        self._processor: AutoProcessor | None = None
        self._model: Qwen2_5_VLForConditionalGeneration | None = None

        # Fixed pipeline prompts
        self.ocr_prompt = EnrichmentAgentSP["extraction"]
        self.describe_prompt = EnrichmentAgentSP["description"]

    def _get_model_and_processor(self) -> tuple[
        AutoProcessor, Qwen2_5_VLForConditionalGeneration]:
        """Lazily loads and handles device distribution for Qwen2.5-VL within the instance."""
        if self._processor is None or self._model is None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Loading VLM Model: %s into memory...", self.model_name)
            self._processor = AutoProcessor.from_pretrained(
                self.model_name,
                cache_dir=str(self.cache_dir)
            )
            self._model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                self.model_name,
                cache_dir=str(self.cache_dir),
                torch_dtype="auto",
                device_map="auto"
            )
        return self._processor, self._model

    def _resize(self, image: Image.Image) -> Image.Image:
        """Resizes the image preserving aspect ratio if it violates maximum boundary thresholds."""
        w, h = image.size
        if max(w, h) <= self.max_image_side:
            return image
        scale = self.max_image_side / max(w, h)
        return image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    def _qwen_infer(self, processor: AutoProcessor,
                    model: Qwen2_5_VLForConditionalGeneration, image: Image.Image,
                    prompt: str) -> str:
        """Executes the standard vision-text model inference sequence."""
        messages = [
            {"role": "user", "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": prompt},
            ]}
        ]
        text = processor.apply_chat_template(messages, tokenize=False,
                                             add_generation_prompt=True)
        image_inputs, video_inputs = process_vision_info(messages)

        inputs = processor(
            text=[text],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt"
        ).to(model.device)

        output_ids = model.generate(**inputs, max_new_tokens=self.max_new_tokens)
        trimmed = output_ids[0][len(inputs.input_ids[0]):]
        return processor.decode(trimmed, skip_special_tokens=True)

    def _process_image_sync(self, b64_string: str) -> dict[str, str]:
        """Synchronous decoding, resizing, and double-pass inference executor."""
        processor, model = self._get_model_and_processor()

        # Base64 string payload bytes restoration
        image_bytes = base64.b64decode(b64_string)
        image = self._resize(Image.open(io.BytesIO(image_bytes)).convert("RGB"))

        # Dual extraction strategy matching the 3-Tier indexing architecture
        caption = self._qwen_infer(processor, model, image, self.describe_prompt)
        ocr_text = self._qwen_infer(processor, model, image, self.ocr_prompt)

        return {"caption": caption, "ocr_text": ocr_text}

    async def enrich_records(self, records: RetrievalOut) -> RetrievalOut:
        """
        Processes each base64 image in the payload inside an external executor thread
        to prevent blocking the main asynchronous orchestrator.
        """
        if not records.image_content:
            return records

        loop = asyncio.get_running_loop()

        for idx, b64_img in enumerate(records.image_content):
            if not b64_img:
                continue

            # Execute CPU/GPU heavy model operations outside the async loop thread
            inference = await loop.run_in_executor(
                None,
                partial(self._process_image_sync, b64_img)
            )

            # Ensure index allocations exist safely before assigning targets
            while len(records.text_content) <= idx:
                records.text_content.append({})

            if not isinstance(records.text_content[idx], dict):
                records.text_content[idx] = {"raw_content": records.text_content[idx]}

            # Append the structured multimodal features back onto the text context wrapper
            records.text_content[idx]["caption"] = inference["caption"]
            records.text_content[idx]["ocr_text"] = inference["ocr_text"]

        return records
