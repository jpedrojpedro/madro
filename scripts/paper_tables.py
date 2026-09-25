#!/usr/bin/env python
"""
Every number the LNBIP paper's evaluation tables need, from the runs already
sitting in allure-results/ — read-only, no pipeline re-execution. Same
scoring as scripts/compare_baseline.py (it reuses its loaders, `compare()`
and `_aggregate()`), just aggregated per tool and per question tier instead
of for one MADRO/Baseline pair.

  - empty-reference questions (tab:golden-standard-empty)
  - MADRO per fusion weight, sampling_depth=25 (tab:fusion-sweep)
  - MADRO per sampling_depth, alpha=0.7/beta=0.3 (tab:sampling-sweep)
  - every Baseline model + MADRO, by tier, with valid SQL translation counts
    (tab:precision-recall-by-type)

Every tool is scored against the same Ground Truth run (the most recent one
unless --ground-truth-run-at is given) over its productive questions.

Usage:
    poetry run python scripts/paper_tables.py
    poetry run python scripts/paper_tables.py --out paper_tables.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv

from scripts.compare_baseline import (
    _aggregate,
    _baseline_model,
    _find_attachment,
    _load_results,
    _question_sort_key,
    _read_json_attachment,
    _select_run,
    _side_result,
    empty_reference_questions,
)
from tests.benchmark.baselines.comparison import BASELINE_SUITE_PREFIX, GROUND_TRUTH_SUITE_PREFIX, RANKS

QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "tests" / "benchmark" / "questions.json"
TIERS = ("low", "medium", "high", "out-of-scope")
TIER_LABELS = {"low": "Low", "medium": "Medium", "high": "High", "out-of-scope": "Challenging"}
BASELINE_MODELS = ("gemini", "qwen2.5-coder", "llama3.1")
FUSION_SWEEP = ((0.3, 0.7), (0.5, 0.5), (0.7, 0.3))
FUSION_SWEEP_SAMPLE = 25
SAMPLING_SWEEP = (10, 25, 100)
SAMPLING_SWEEP_WEIGHTS = (0.7, 0.3)
TOOL_SAMPLE = 100
# NaiveSQLBaseline.MAX_ATTEMPTS — imported by value to keep this script free
# of madro's Django settings.
MAX_SQL_ATTEMPTS = 5


def _tier(question: dict) -> str:
    complexity = question["complexity"]
    return next(t for t in TIERS if complexity.startswith(t))


def _sql_executes(sql_text: str) -> bool:
    """Read-only re-execution — only for runs recorded before the
    `sql_error` provenance field existed, where a 5-attempt outcome can't
    otherwise be told apart from an executed-but-empty one."""
    with psycopg.connect(os.environ["RETRIEVAL_DB_URL"]) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = 5000")
                cur.execute(sql_text)
            return True
        except psycopg.Error:
            return False
        finally:
            conn.rollback()


def _agent_sql_valid(artifact: dict) -> bool:
    if artifact.get("status") != "completed":
        return False
    provenance = artifact.get("provenance") or {}
    if "sql_error" in provenance:
        return provenance["sql_error"] is None
    if provenance.get("sql_attempts", MAX_SQL_ATTEMPTS) < MAX_SQL_ATTEMPTS:
        return True
    sql_text = provenance.get("generated_sql")
    return bool(sql_text) and _sql_executes(sql_text)


def _madro_valid(allure_dir: Path, entry: dict) -> bool:
    """A valid SQL translation, for MADRO: at least one of the question's
    Retrieval Agents ran an SQL query that executed without error."""
    artifacts = _read_json_attachment(allure_dir, entry, "Artifacts") or []
    return any(_agent_sql_valid(artifact) for artifact in artifacts)


def _score(per_question: dict, question_ids: list[str]) -> dict:
    summary = _aggregate({q: per_question[q] for q in question_ids}, "side")
    return {
        "n": len(question_ids),
        **{f"P@{k}": summary[k]["precision_mean"] for k in RANKS},
        **{f"R@{k}": summary[k]["recall_mean"] for k in RANKS},
        "identity_match_rate": summary["identity_match_rate"],
        "identity_match_n": summary["identity_match_n"],
    }


def _tool_table(
    per_question: dict, valid: dict[str, bool], productive: list[str], tier_by_id: dict[str, str]
) -> dict:
    rows = {"Total": productive}
    rows |= {TIER_LABELS[t]: [q for q in productive if tier_by_id[q] == t] for t in TIERS}
    return {
        label: {**_score(per_question, ids), "valid_sql": sum(valid[q] for q in ids)}
        for label, ids in rows.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allure-dir", default="allure-results", type=Path)
    parser.add_argument("--ground-truth-run-at", default=None)
    parser.add_argument("--out", default="paper_tables.json", type=Path)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    questions = json.loads(QUESTIONS_PATH.read_text())
    tier_by_id = {q["id"]: _tier(q) for q in questions}
    all_results = _load_results(args.allure_dir)

    ground_truth_entries, ground_truth_run_at = _select_run(
        [r for r in all_results if r["_labels"].get("parentSuite", "").startswith(GROUND_TRUTH_SUITE_PREFIX)],
        args.ground_truth_run_at,
    )
    reference_by_id: dict[str, list[dict]] = {}
    reference_errors: dict[str, str] = {}
    for entry in ground_truth_entries:
        payload = _read_json_attachment(args.allure_dir, entry, "Ground Truth result") or {}
        if "error" in payload:
            reference_errors[entry["_labels"]["story"]] = payload["error"]
        else:
            reference_by_id[entry["_labels"]["story"]] = payload.get("rows") or []

    empty = empty_reference_questions(reference_by_id)
    productive = sorted(
        (q["id"] for q in questions if q["id"] in reference_by_id and q["id"] not in empty),
        key=_question_sort_key,
    )

    def _per_question(retrieved_by_id: dict, retrieved_errors: dict) -> dict:
        return {
            q: {"side": _side_result(reference_by_id, reference_errors, retrieved_by_id, retrieved_errors, q)}
            for q in productive
        }

    def _madro(sample: int, alpha: float, beta: float) -> tuple[dict, dict[str, bool], str | None]:
        approach = f"sample-{sample}_alpha-{alpha}_beta-{beta}"
        entries, run_at = _select_run([r for r in all_results if r["_parameters"].get("approach") == approach], None)
        if not entries:
            return {}, {}, None
        by_story = {e["_labels"]["story"]: e for e in entries}
        retrieved = {q: _read_json_attachment(args.allure_dir, e, "Ranked entities") or [] for q, e in by_story.items()}
        valid = {q: q in by_story and _madro_valid(args.allure_dir, by_story[q]) for q in productive}
        return _per_question(retrieved, {}), valid, f"{approach} @ {run_at}"

    def _baseline(model: str) -> tuple[dict, dict[str, bool], str | None]:
        entries, run_at = _select_run(
            [
                r for r in all_results
                if r["_labels"].get("parentSuite", "").startswith(BASELINE_SUITE_PREFIX) and _baseline_model(r) == model
            ],
            None,
        )
        if not entries:
            return {}, {}, None
        retrieved, errors = {}, {}
        for entry in entries:
            payload = _read_json_attachment(args.allure_dir, entry, "Baseline result") or {}
            if "error" in payload:
                errors[entry["_labels"]["story"]] = payload["error"]
            else:
                retrieved[entry["_labels"]["story"]] = payload.get("rows") or []
        valid = {q: q in retrieved for q in productive}
        return _per_question(retrieved, errors), valid, f"Baseline_{model} @ {run_at}"

    output: dict = {
        "ground_truth_run": f"{GROUND_TRUTH_SUITE_PREFIX}gemini @ {ground_truth_run_at}",
        "empty_reference_questions": [
            {
                "id": q["id"], "tier": tier_by_id[q["id"]], "prompt": q.get("rephrase") or q["prompt"],
                "rows": len(reference_by_id.get(q["id"], [])),
            }
            for q in questions if q["id"] in empty
        ],
        "reference_errors": reference_errors,
        "productive_n": len(productive),
        "productive_by_tier": {TIER_LABELS[t]: sum(tier_by_id[q] == t for q in productive) for t in TIERS},
        "fusion_sweep": {},
        "sampling_sweep": {},
        "by_type": {},
    }

    for alpha, beta in FUSION_SWEEP:
        per_question, _, run = _madro(FUSION_SWEEP_SAMPLE, alpha, beta)
        output["fusion_sweep"][f"{alpha}/{beta}"] = {"run": run, **(_score(per_question, productive) if run else {})}
    for sample in SAMPLING_SWEEP:
        per_question, _, run = _madro(sample, *SAMPLING_SWEEP_WEIGHTS)
        output["sampling_sweep"][str(sample)] = {"run": run, **(_score(per_question, productive) if run else {})}

    tools = [(f"Baseline {m}", _baseline(m)) for m in BASELINE_MODELS]
    tools.append((f"MADRO sample-{TOOL_SAMPLE}", _madro(TOOL_SAMPLE, *SAMPLING_SWEEP_WEIGHTS)))
    for label, (per_question, valid, run) in tools:
        output["by_type"][label] = {"run": run, **(_tool_table(per_question, valid, productive, tier_by_id) if run else {})}

    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
