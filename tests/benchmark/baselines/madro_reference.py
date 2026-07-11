"""
Looks up which identity field an existing MADRO run resolved to for a given
question, so the naive SQL baseline can be told the same field name up
front. Without this, the baseline naturally answers at whatever entity
granularity its own query implies (e.g. `comment_id`), which can differ from
whatever `EntityResolver` picked for that question (e.g. `profile_id`,
chosen from the fields shared across that thread's retrieval agents) — a
mismatch that shows up as a false 0% overlap in the comparison, not an
actual quality gap.

Only the field NAME is exposed to the baseline (e.g. "profile_id") — never
MADRO's actual entities, IDs, or ranking. This is a comparison-methodology
fix, not a hint about the answer.
"""

import json
from collections import Counter
from pathlib import Path

from tests.benchmark.baselines.comparison import identity


def _load_madro_results(allure_dir: Path) -> list[dict]:
    results = []
    for path in sorted(allure_dir.glob("*-result.json")):
        data = json.loads(path.read_text())
        labels = {label["name"]: label["value"] for label in data.get("labels", [])}
        if not labels.get("parentSuite") or labels["parentSuite"] == "baseline":
            continue
        data["_labels"] = labels
        results.append(data)
    return results


def _find_attachment(result: dict, name: str) -> dict | None:
    for attachment in result.get("attachments", []):
        if attachment["name"] == name:
            return attachment
    for step in result.get("steps", []):
        for attachment in step.get("attachments", []):
            if attachment["name"] == name:
                return attachment
    return None


def build_identity_hints(allure_dir: Path) -> dict[str, str]:
    """question_id -> most common identity field name across every existing
    MADRO run found for that question (any approach/run_at — the field is
    driven by which retrieval agents fired for the question, not by
    alpha/beta weights, so agreement across runs is expected). Questions
    with no MADRO reference yet, or no resolvable identity field, are
    omitted — callers should treat a missing hint as "fall back to the
    baseline's generic instructions", not as an error."""
    if not allure_dir.exists():
        return {}

    counts_by_question: dict[str, Counter] = {}
    for result in _load_madro_results(allure_dir):
        question_id = result["_labels"].get("story")
        if not question_id:
            continue
        attachment = _find_attachment(result, "Ranked entities")
        if attachment is None:
            continue
        ranked = json.loads((allure_dir / attachment["source"]).read_text())
        counter = counts_by_question.setdefault(question_id, Counter())
        for entity in ranked:
            key = identity(entity.get("entity_data", {}))
            if key:
                counter[key[0]] += 1

    return {
        question_id: counter.most_common(1)[0][0]
        for question_id, counter in counts_by_question.items()
        if counter
    }
