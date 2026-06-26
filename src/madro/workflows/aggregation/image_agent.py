"""
Image inference and aggregation.

describe_images():
  For each image record (base64):
    1. Qwen2.5-VL → caption (visual scene description)
    2. Qwen2.5-VL → ocr_text (visible text extraction)
  Returns enriched records with caption + ocr_text, raw bytes stripped.

aggregate_image():
  Pure vector scoring against stored embeddings — symmetric with aggregate_text.
  S_i(e) = γ · S_vis(e) + δ · S_sem_i(e)
"""

import base64
import io
import json

import numpy as np
from PIL import Image
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import _get_encoder, _CACHE_DIR
from madro.data_wrappers import RetrievalOut


_QWEN_MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"

_qwen_processor: AutoProcessor | None = None
_qwen_model: Qwen2_5_VLForConditionalGeneration | None = None


def _get_qwen():
    global _qwen_processor, _qwen_model
    if _qwen_processor is None:
        _qwen_processor = AutoProcessor.from_pretrained(_QWEN_MODEL, cache_dir=str(_CACHE_DIR))
        _qwen_model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            _QWEN_MODEL, cache_dir=str(_CACHE_DIR), torch_dtype="auto", device_map="auto"
        )
    return _qwen_processor, _qwen_model


_OCR_PROMPT = "Extract all text from this image.\n\nReturn markdown preserving structure."
_DESCRIBE_PROMPT = "Describe this image in detail."


def _qwen_infer(processor, model, image: Image.Image, prompt: str) -> str:
    messages = [
        {"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ]}
    ]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(
        text=[text], images=image_inputs, videos=video_inputs, padding=True, return_tensors="pt"
    ).to(model.device)
    output_ids = model.generate(**inputs, max_new_tokens=1024)
    trimmed = output_ids[0][len(inputs.input_ids[0]):]
    return processor.decode(trimmed, skip_special_tokens=True)


_MAX_SIDE = 1024


def _resize(image: Image.Image) -> Image.Image:
    w, h = image.size
    if max(w, h) <= _MAX_SIDE:
        return image
    scale = _MAX_SIDE / max(w, h)
    return image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def _infer_sync(b64: str) -> dict:
    image = _resize(Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB"))
    processor, model = _get_qwen()
    caption = _qwen_infer(processor, model, image, _DESCRIBE_PROMPT)
    ocr_text = _qwen_infer(processor, model, image, _OCR_PROMPT)
    return {"caption": caption, "ocr_text": ocr_text}


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


def describe_images(records: RetrievalOut) -> RetrievalOut:
    """Run Qwen2.5-VL on each record, return enriched records without raw data."""
    for idx, img in enumerate(records.image_content):
        if img:
            inference = _infer_sync(img)
            records.text_content[idx]["caption"] = inference["caption"]
            records.text_content[idx]["ocr_text"] = inference["ocr_text"]
    return records


async def aggregate_image(thread_id: str, demand: str) -> dict[str, float]:
    """Return S_i scores keyed by publication_id — pure vector scoring against stored embeddings."""
    cfg = load_config()
    gamma = cfg.fusion.image.gamma
    delta = cfg.fusion.image.delta

    encoder = _get_encoder()
    query_embedding = encoder.encode(demand).tolist()
    vector_literal = "[" + ",".join(map(str, query_embedding)) + "]"

    async with async_cursor() as cur:
        await cur.execute(
            """
            SELECT jad.job_artifact_id,
                   1 - (jad.embedding <=> %s::vector) AS sem_score,
                   ja.canonical_text
            FROM broker.job_artifact_document jad
            JOIN broker.job_artifact ja ON ja.job_status_id = jad.job_artifact_id
            JOIN broker.job_status js ON js.id = ja.job_status_id
            JOIN broker.job_execution je ON je.job_id = js.job_id AND je.agent_id = js.agent_id
            JOIN agents_topics.agent a ON a.id = je.agent_id
            WHERE je.thread_id = %s
              AND js.status = 'completed'
              AND a.modality = 'image'
            """,
            [vector_literal, thread_id],
        )
        rows = await cur.fetchall()

    if not rows:
        return {}

    scores: dict[str, float] = {}

    for row in rows:
        _, sem_score, canonical_text = row
        try:
            records = json.loads(canonical_text)
            if not isinstance(records, list):
                records = [records]
        except (json.JSONDecodeError, TypeError):
            continue
        for record in records:
            pub_id = str(record.get("publication_id", ""))
            s_image = gamma * float(sem_score) + delta * float(sem_score)
            scores[pub_id] = max(scores.get(pub_id, 0.0), s_image)

    return scores
