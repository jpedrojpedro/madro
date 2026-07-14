import base64
import io
import logging
from PIL import Image

from pydantic_ai import Agent, BinaryContent

from madro.data_wrappers import RetrievalOut
from madro.config import get_image_model
from madro.internal_agents.system_prompts import EnrichmentAgentSP

logger = logging.getLogger(__name__)

# Ollama's default num_ctx (2048) silently truncates rather than erroring once an
# image's vision tokens plus the OCR/caption prompt exceed it — give it headroom.
IMAGE_MODEL_NUM_CTX = 8192


class EnrichmentAgent:
    def __init__(
            self,
            max_image_side: int = 1024,
            max_new_tokens: int = 1024,
    ):
        self.max_image_side = max_image_side

        self._agent = Agent(
            get_image_model(),
            model_settings={
                "max_tokens": max_new_tokens,
                "extra_body": {"options": {"num_ctx": IMAGE_MODEL_NUM_CTX}},
            },
        )

        # Fixed pipeline prompts
        self.ocr_prompt = EnrichmentAgentSP["extraction"]
        self.describe_prompt = EnrichmentAgentSP["description"]

    def _resize(self, image: Image.Image) -> Image.Image:
        """Resizes the image preserving aspect ratio if it violates maximum boundary thresholds."""
        w, h = image.size
        if max(w, h) <= self.max_image_side:
            return image
        scale = self.max_image_side / max(w, h)
        return image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    async def _qwen_infer(self, image_bytes: bytes, prompt: str) -> str:
        """Runs a single vision prompt against the Ollama-served model."""
        result = await self._agent.run([prompt, BinaryContent(data=image_bytes, media_type="image/jpeg")])
        return result.output

    async def _process_image(self, b64_string: str) -> dict[str, str]:
        """Decodes, resizes, and runs the dual extraction pass on one image."""
        image_bytes = base64.b64decode(b64_string)
        image = self._resize(Image.open(io.BytesIO(image_bytes)).convert("RGB"))

        buf = io.BytesIO()
        image.save(buf, format="JPEG")
        jpeg_bytes = buf.getvalue()

        # Dual extraction strategy matching the 3-Tier indexing architecture
        img_caption = await self._qwen_infer(jpeg_bytes, self.describe_prompt)
        ocr_text = await self._qwen_infer(jpeg_bytes, self.ocr_prompt)

        return {"img_caption": img_caption, "ocr_text": ocr_text}

    async def enrich_records(self, records: RetrievalOut) -> RetrievalOut:
        """
        Processes each base64 image in the payload, merging captions/OCR text
        back into the record's text content.
        """
        if not records.image_content:
            return records

        for idx, b64_img in enumerate(records.image_content):
            if not b64_img:
                continue

            inference = await self._process_image(b64_img)

            # Ensure index allocations exist safely before assigning targets
            while len(records.text_content) <= idx:
                records.text_content.append({})

            if not isinstance(records.text_content[idx], dict):
                records.text_content[idx] = {"raw_content": records.text_content[idx]}

            # Append the structured multimodal features back onto the text context wrapper
            records.text_content[idx]["img_caption"] = inference["img_caption"]
            records.text_content[idx]["ocr_text"] = inference["ocr_text"]

        return records
