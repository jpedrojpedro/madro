"""
Looks up which identity column Ground Truth resolved to for each question,
so Baseline can be told the same field name up front. Ground Truth is now
the authority on entity granularity — see
docs/adr/0001-ground-truth-is-the-benchmark-reference.md — replacing the old
MADRO-run-voting approach this module's predecessor (madro_reference.py)
implemented.

Only the field NAME is exposed to Baseline (e.g. "profile_id") — never
Ground Truth's actual rows or SQL. This is a comparison-methodology fix, not
a hint about the answer.
"""

import json
from pathlib import Path

from tests.benchmark.baselines.comparison import GROUND_TRUTH_SUITE_PREFIX, identity


def _load_ground_truth_results(allure_dir: Path) -> list[dict]:
    results = []
    for path in sorted(allure_dir.glob("*-result.json")):
        data = json.loads(path.read_text())
        labels = {label["name"]: label["value"] for label in data.get("labels", [])}
        if not labels.get("parentSuite", "").startswith(GROUND_TRUTH_SUITE_PREFIX):
            continue
        data["_labels"] = labels
        data["_parameters"] = {p["name"]: p["value"] for p in data.get("parameters", [])}
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
    """question_id -> identity field name, read directly off the most recent
    Ground Truth run's own resolved rows for that question — one run
    produces one row set per question, so (unlike the old MADRO-derived
    version of this function) there's nothing to vote across. Questions with
    no Ground Truth run yet, a Ground Truth error, an empty result, or no
    resolvable identity field are omitted — callers should treat a missing
    hint as "fall back to Baseline's generic instructions", not as an
    error."""
    if not allure_dir.exists():
        return {}

    results = _load_ground_truth_results(allure_dir)
    if not results:
        return {}

    most_recent_run_at = max(r["_parameters"].get("run_at", "") for r in results)

    hints: dict[str, str] = {}
    for result in results:
        if result["_parameters"].get("run_at") != most_recent_run_at:
            continue
        question_id = result["_labels"].get("story")
        if not question_id:
            continue
        attachment = _find_attachment(result, "Ground Truth result")
        if attachment is None:
            continue
        payload = json.loads((allure_dir / attachment["source"]).read_text())
        rows = payload.get("rows") or []
        if not rows:
            continue
        key = identity(rows[0])
        if key:
            hints[question_id] = key[0]

    return hints
