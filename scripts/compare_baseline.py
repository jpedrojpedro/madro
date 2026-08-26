#!/usr/bin/env python
"""
Compares existing Ground Truth, Baseline, and MADRO benchmark runs' Allure
results against each other — all three already sitting in allure-results/,
so this never re-runs any pipeline. Ground Truth is the reference
("relevant") set for both comparisons — see
docs/adr/0001-ground-truth-is-the-benchmark-reference.md.

Produced by:
    tests/benchmark/test_ground_truth.py  (Ground Truth runs, parent_suite
                                           "GroundTruth_gemini @ {timestamp}")
    tests/benchmark/test_baseline.py      (the baseline runs, one per model, parent_suite
                                           "Baseline_{model} @ {timestamp}")
    tests/benchmark/test_benchmark.py     (the MADRO "approach" runs, parent_suite
                                           "{approach} @ {timestamp}")

Usage:
    poetry run python scripts/compare_baseline.py --approach sample-10_alpha-0.0_beta-1.0
    poetry run python scripts/compare_baseline.py --approach sample-10_alpha-0.0_beta-1.0 \\
        --baseline-model qwen2.5-coder \\
        --run-at 2026-07-09T17:35:04Z --baseline-run-at 2026-07-10T12:00:00Z \\
        --ground-truth-run-at 2026-07-08T09:00:00Z \\
        --out my_comparison.json --grid-out my_grid.xlsx

Outputs two files:
  --out (comparison_results.json): precision/recall@1/5/10 + positional diff
    + identity-match flag for Baseline-vs-Ground-Truth and MADRO-vs-Ground-
    Truth, for the ONE approach selected via --approach and the ONE baseline
    model selected via --baseline-model (default: gemini — test_baseline.py
    runs the naive SQL baseline against both gemini and qwen2.5-coder, tagged
    via each result's "baseline_model" Allure parameter).
  --grid-out (comparison_grid.xlsx): a wide grid, one column-block per
    question (one "ground-truth" column + one per baseline model found +
    one per MADRO approach found in allure-results, most recent run of
    each), rows = ranked identity at positions 1..10. Identity values
    repeated within a question's block are fill-colored so overlaps are
    visible at a glance. Independent of --approach/--baseline-model — it
    always includes every approach and every baseline model found.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from tabulate import tabulate

from tests.benchmark.baselines.comparison import (
    BASELINE_SUITE_PREFIX,
    GROUND_TRUTH_SUITE_PREFIX,
    RANKS,
    compare,
    identity,
)

# Runs recorded before the "baseline_model" Allure parameter existed only
# ever used Gemini, so a missing parameter defaults to it rather than an
# "unknown" bucket.
DEFAULT_BASELINE_MODEL = "gemini"

GRID_ROWS = 10
GRID_PALETTE = [
    "FFF2CC", "D9EAD3", "CFE2F3", "F4CCCC", "D9D2E9",
    "FCE5CD", "D0E0E3", "EAD1DC", "C9DAF8", "B6D7A8",
]


def _unwrap(value: str) -> str:
    """Allure serializes `parameter()` values via repr(), so plain strings
    come back with their quotes still embedded — labels aren't affected."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _load_results(allure_dir: Path) -> list[dict]:
    results = []
    for path in allure_dir.glob("*-result.json"):
        data = json.loads(path.read_text())
        data["_labels"] = {label["name"]: label["value"] for label in data.get("labels", [])}
        data["_parameters"] = {p["name"]: _unwrap(p["value"]) for p in data.get("parameters", [])}
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


def _read_json_attachment(allure_dir: Path, result: dict, name: str) -> dict | list | None:
    attachment = _find_attachment(result, name)
    if attachment is None:
        return None
    return json.loads((allure_dir / attachment["source"]).read_text())


def _select_run(entries: list[dict], run_at: str | None) -> tuple[list[dict], str | None]:
    if not entries:
        return [], None
    if run_at is None:
        run_at = max(e["_parameters"].get("run_at", "") for e in entries)
    return [e for e in entries if e["_parameters"].get("run_at") == run_at], run_at


def _identity_str(record: dict) -> str | None:
    key = identity(record)
    return f"{key[0]}={key[1]}" if key else None


def _madro_identity_lists(allure_dir: Path, entries: list[dict]) -> dict[str, list[str]]:
    by_id = {}
    for entry in entries:
        question_id = entry["_labels"].get("story")
        ranked = _read_json_attachment(allure_dir, entry, "Ranked entities") or []
        ids = [_identity_str(e["entity_data"]) for e in ranked]
        by_id[question_id] = [i for i in ids if i][:GRID_ROWS]
    return by_id


def _identity_lists_from_rows_attachment(
    allure_dir: Path, entries: list[dict], attachment_name: str
) -> dict[str, list[str]]:
    """Baseline and Ground Truth both attach `{"rows": [...]}` (or
    `{"error": ...}`) under their own attachment name — same payload shape,
    just a different label."""
    by_id = {}
    for entry in entries:
        question_id = entry["_labels"].get("story")
        payload = _read_json_attachment(allure_dir, entry, attachment_name) or {}
        ids = [_identity_str(row) for row in (payload.get("rows") or [])]
        by_id[question_id] = [i for i in ids if i][:GRID_ROWS]
    return by_id


def _baseline_model(entry: dict) -> str:
    return entry["_parameters"].get("baseline_model") or DEFAULT_BASELINE_MODEL


def _baseline_identity_lists_by_model(allure_dir: Path, entries: list[dict]) -> dict[str, dict[str, list[str]]]:
    """One {question_id: ids} map per distinct baseline model found, each
    built from that model's most recent run (independently of any other
    model's run_at)."""
    entries_by_model: dict[str, list[dict]] = {}
    for entry in entries:
        entries_by_model.setdefault(_baseline_model(entry), []).append(entry)

    result = {}
    for model_key, model_entries in entries_by_model.items():
        selected, _ = _select_run(model_entries, None)
        result[model_key] = _identity_lists_from_rows_attachment(allure_dir, selected, "Baseline result")
    return result


def _question_sort_key(question_id: str) -> int:
    match = re.match(r"Q(\d+)", question_id or "")
    return int(match.group(1)) if match else 10_000


def build_comparison_grid(
    allure_dir: Path, all_results: list[dict], baseline_entries: list[dict], ground_truth_entries: list[dict]
) -> Workbook:
    """One column-block per question: "ground-truth" + every baseline model
    found + every MADRO approach found in allure-results (most recent run of
    each), sorted by alpha descending. Rows are rank positions 1..GRID_ROWS.
    Identity values repeated within a question's block (across ground truth,
    baselines, and/or approaches) get a shared fill color, so overlaps are
    visible at a glance."""
    approaches_raw: dict[str, list[dict]] = {}
    for r in all_results:
        approach = r["_parameters"].get("approach")
        if approach:
            approaches_raw.setdefault(approach, []).append(r)

    approach_labels = []
    for approach in approaches_raw:
        m = re.search(r"alpha-([\d.]+)_beta-([\d.]+)", approach)
        if m:
            approach_labels.append((float(m.group(1)), float(m.group(2)), approach))
    approach_labels.sort(key=lambda t: -t[0])

    ground_truth_selected, _ = _select_run(ground_truth_entries, None)
    ground_truth_ids = _identity_lists_from_rows_attachment(allure_dir, ground_truth_selected, "Ground Truth result")

    baseline_ids_by_model = _baseline_identity_lists_by_model(allure_dir, baseline_entries)
    baseline_model_keys = sorted(baseline_ids_by_model)
    approach_ids = {}
    for _, _, approach in approach_labels:
        entries, _ = _select_run(approaches_raw[approach], None)
        approach_ids[approach] = _madro_identity_lists(allure_dir, entries)

    question_ids = set(ground_truth_ids)
    for ids_by_question in baseline_ids_by_model.values():
        question_ids |= set(ids_by_question)
    for ids_by_question in approach_ids.values():
        question_ids |= set(ids_by_question)
    question_ids = sorted(question_ids, key=_question_sort_key)

    wb = Workbook()
    ws = wb.active
    ws.title = "Comparison Grid"

    col = 1
    for question_id in question_ids:
        columns = [("ground-truth", ground_truth_ids.get(question_id, []))]
        columns += [
            (f"baseline-{model_key}", baseline_ids_by_model[model_key].get(question_id, []))
            for model_key in baseline_model_keys
        ]
        for alpha, beta, approach in approach_labels:
            columns.append((f"alpha-{alpha}_beta-{beta}", approach_ids[approach].get(question_id, [])))

        start_col = col
        for name, ids in columns:
            header_cell = ws.cell(row=1, column=col, value=f"{question_id}-{name}")
            header_cell.font = Font(bold=True)
            for i, value in enumerate(ids):
                ws.cell(row=2 + i, column=col, value=value)
            ws.column_dimensions[header_cell.column_letter].width = 26
            col += 1
        end_col = col - 1

        counts: dict[str, int] = {}
        for _, ids in columns:
            for value in ids:
                counts[value] = counts.get(value, 0) + 1
        color_by_value = {
            value: GRID_PALETTE[i % len(GRID_PALETTE)]
            for i, value in enumerate(v for v, c in sorted(counts.items()) if c >= 2)
        }

        for c in range(start_col, end_col + 1):
            for r in range(2, 2 + GRID_ROWS):
                cell = ws.cell(row=r, column=c)
                if cell.value in color_by_value:
                    color = color_by_value[cell.value]
                    cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")

    ws.freeze_panes = "B2"
    return wb


def _side_result(reference_by_id: dict, reference_errors: dict, retrieved_by_id: dict, retrieved_errors: dict, question_id: str) -> dict:
    if question_id in reference_errors:
        return {"status": "ground_truth_error", "error": reference_errors[question_id]}
    if question_id not in reference_by_id:
        return {"status": "missing_in_ground_truth"}
    if question_id in retrieved_errors:
        return {"status": "error", "error": retrieved_errors[question_id]}
    if question_id not in retrieved_by_id:
        return {"status": "missing"}
    return {"status": "ok", **compare(reference_by_id[question_id], retrieved_by_id[question_id])}


def _print_side_table(title: str, complexities: dict[str, str], per_question: dict, side: str) -> None:
    rows = []
    for question_id in sorted(per_question, key=_question_sort_key):
        result = per_question[question_id][side]
        complexity = complexities.get(question_id, "")
        if result["status"] != "ok":
            rows.append([question_id, complexity, result["status"], "", "", ""])
            continue
        rows.append([
            question_id,
            complexity,
            "ok",
            " / ".join(f"{result['metrics'][k]['precision']:.2f}" for k in RANKS),
            " / ".join(f"{result['metrics'][k]['recall']:.2f}" for k in RANKS),
            result["identity_match"],
        ])
    print(f"\n{title}")
    print(tabulate(rows, headers=["id", "complexity", "status", "precision@1/5/10", "recall@1/5/10", "identity_match"]))


def _aggregate(per_question: dict, side: str) -> dict:
    values = {k: {"precision": [], "recall": []} for k in RANKS}
    identity_matches = []
    for entry in per_question.values():
        result = entry[side]
        if result["status"] != "ok":
            continue
        for k in RANKS:
            values[k]["precision"].append(result["metrics"][k]["precision"])
            values[k]["recall"].append(result["metrics"][k]["recall"])
        if result["identity_match"] is not None:
            identity_matches.append(result["identity_match"])

    summary = {
        k: {
            "precision_mean": sum(v["precision"]) / len(v["precision"]) if v["precision"] else None,
            "recall_mean": sum(v["recall"]) / len(v["recall"]) if v["recall"] else None,
            "n": len(v["precision"]),
        }
        for k, v in values.items()
    }
    summary["identity_match_rate"] = (
        sum(identity_matches) / len(identity_matches) if identity_matches else None
    )
    summary["identity_match_n"] = len(identity_matches)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approach", required=True, help="MADRO run label, e.g. sample-10_alpha-0.0_beta-1.0")
    parser.add_argument("--baseline-model", default=DEFAULT_BASELINE_MODEL, help="Which baseline model to compare against for --out, e.g. gemini or qwen2.5-coder (default: gemini)")
    parser.add_argument("--run-at", default=None, help="Disambiguate if --approach was run more than once (default: most recent)")
    parser.add_argument("--baseline-run-at", default=None, help="Disambiguate if the baseline model was run more than once (default: most recent)")
    parser.add_argument("--ground-truth-run-at", default=None, help="Disambiguate if Ground Truth was run more than once (default: most recent)")
    parser.add_argument("--allure-dir", default="allure-results", type=Path)
    parser.add_argument("--out", default="comparison_results.json", type=Path)
    parser.add_argument("--grid-out", default="comparison_grid.xlsx", type=Path, help="Excel grid: ground truth + every baseline model + every approach found, ranked identities per question, color-coded overlaps")
    args = parser.parse_args()

    all_results = _load_results(args.allure_dir)

    ground_truth_all = [
        r for r in all_results if r["_labels"].get("parentSuite", "").startswith(GROUND_TRUTH_SUITE_PREFIX)
    ]
    ground_truth_entries, ground_truth_run_at = _select_run(ground_truth_all, args.ground_truth_run_at)
    if not ground_truth_entries:
        print(f"No Ground Truth results found in {args.allure_dir} — run `make benchmark-ground-truth` first", file=sys.stderr)
        sys.exit(1)

    madro_candidates = [r for r in all_results if r["_parameters"].get("approach") == args.approach]
    madro_entries, madro_run_at = _select_run(madro_candidates, args.run_at)
    if not madro_entries:
        print(f"No MADRO results found for approach={args.approach!r} in {args.allure_dir}", file=sys.stderr)
        sys.exit(1)

    all_baseline_entries = [
        r for r in all_results if r["_labels"].get("parentSuite", "").startswith(BASELINE_SUITE_PREFIX)
    ]
    baseline_candidates = [r for r in all_baseline_entries if _baseline_model(r) == args.baseline_model]
    baseline_entries, baseline_run_at = _select_run(baseline_candidates, args.baseline_run_at)
    if not baseline_entries:
        print(
            f"No baseline results found for baseline_model={args.baseline_model!r} in {args.allure_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"Comparing ground_truth (run_at={ground_truth_run_at}) against "
        f"approach={args.approach!r} (run_at={madro_run_at}) and "
        f"baseline_model={args.baseline_model!r} (run_at={baseline_run_at})"
    )

    complexities: dict[str, str] = {}
    ground_truth_by_id: dict[str, list[dict]] = {}
    ground_truth_errors: dict[str, str] = {}
    for entry in ground_truth_entries:
        question_id = entry["_labels"].get("story")
        payload = _read_json_attachment(args.allure_dir, entry, "Ground Truth result") or {}
        if "error" in payload:
            ground_truth_errors[question_id] = payload["error"]
        else:
            ground_truth_by_id[question_id] = payload.get("rows") or []

    madro_by_id: dict[str, list[dict]] = {}
    for entry in madro_entries:
        question_id = entry["_labels"].get("story")
        complexities[question_id] = entry["_labels"].get("feature", "")
        ranked = _read_json_attachment(args.allure_dir, entry, "Ranked entities") or []
        madro_by_id[question_id] = [e["entity_data"] for e in ranked]

    baseline_by_id: dict[str, list[dict]] = {}
    baseline_errors: dict[str, str] = {}
    for entry in baseline_entries:
        question_id = entry["_labels"].get("story")
        complexities.setdefault(question_id, entry["_labels"].get("feature", ""))
        payload = _read_json_attachment(args.allure_dir, entry, "Baseline result") or {}
        if "error" in payload:
            baseline_errors[question_id] = payload["error"]
        else:
            baseline_by_id[question_id] = payload.get("rows") or []

    all_ids = set(ground_truth_by_id) | set(ground_truth_errors) | set(madro_by_id) | set(baseline_by_id) | set(baseline_errors)
    per_question = {}
    for question_id in all_ids:
        per_question[question_id] = {
            "baseline_vs_ground_truth": _side_result(
                ground_truth_by_id, ground_truth_errors, baseline_by_id, baseline_errors, question_id
            ),
            "madro_vs_ground_truth": _side_result(
                ground_truth_by_id, ground_truth_errors, madro_by_id, {}, question_id
            ),
        }

    _print_side_table("Baseline vs Ground Truth", complexities, per_question, "baseline_vs_ground_truth")
    _print_side_table("MADRO vs Ground Truth", complexities, per_question, "madro_vs_ground_truth")

    aggregate = {
        "baseline_vs_ground_truth": _aggregate(per_question, "baseline_vs_ground_truth"),
        "madro_vs_ground_truth": _aggregate(per_question, "madro_vs_ground_truth"),
    }
    print("\nAggregate (mean over questions with a comparable Ground Truth result):")
    print(tabulate(
        [[side, k, v["precision_mean"], v["recall_mean"], v["n"]] for side, summary in aggregate.items() for k, v in summary.items() if k in RANKS],
        headers=["side", "k", "precision_mean", "recall_mean", "n"],
    ))
    print(tabulate(
        [[side, summary["identity_match_rate"], summary["identity_match_n"]] for side, summary in aggregate.items()],
        headers=["side", "identity_match_rate", "identity_match_n"],
    ))

    output = {
        "approach": args.approach,
        "baseline_model": args.baseline_model,
        "madro_run_at": madro_run_at,
        "baseline_run_at": baseline_run_at,
        "ground_truth_run_at": ground_truth_run_at,
        "aggregate": aggregate,
        "per_question": per_question,
    }
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\nWrote {args.out}")

    grid = build_comparison_grid(args.allure_dir, all_results, all_baseline_entries, ground_truth_all)
    grid.save(args.grid_out)
    print(f"Wrote {args.grid_out}")


if __name__ == "__main__":
    main()
