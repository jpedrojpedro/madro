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
  --grid-out (comparison_grid.xlsx): one row-block of GRID_ROWS rows per
    question (question_id + question text, in questions.json order), with a
    blank separator row between blocks. Columns: question_id, question,
    ground-truth, the --baseline-model baseline, and one column per distinct
    alpha/beta MADRO weighting found in allure-results. When multiple
    approaches (different sample sizes) share an alpha/beta pair, only the
    one with the most recent run is kept — older sample sizes at the same
    weighting are superseded, not meant to coexist in the comparison.
    Identity values repeated within a question's block are fill-colored so
    overlaps are visible at a glance. Independent of --approach — always
    includes every alpha/beta weighting found — but scoped to the one
    --baseline-model.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
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

# Questions dropped entirely from precision/recall (both sides) and from the
# printed per-question tables — verified directly against the GroundTruth_gemini
# run (2026-08-28T21:25:17Z) that each returns 0 rows, so there is nothing for
# either side to be scored against. Kept here (rather than silently scoring as
# a false 0 — see _side_result, which treats an empty-but-present rows/ranked
# list as a real "ok" comparison, not "missing") so the exclusion is visible
# and its reason is on record. Q30 is deliberately NOT here: its Gemini
# content-filter block happens during MADRO's Response Synthesis step, after
# retrieval and ranking already completed — its ranked-entities comparison
# against Ground Truth is a genuine (if poor) result, not a data artifact.
EXCLUDED_QUESTIONS: dict[str, str] = {
    "Q06": "Ground Truth query returns 0 rows (empty Golden Standard)",
    "Q07": "Ground Truth query returns 0 rows (empty Golden Standard)",
    "Q08": "Ground Truth query returns 0 rows (empty Golden Standard)",
    "Q11": "Ground Truth query returns 0 rows (empty Golden Standard)",
    "Q47": "Ground Truth query returns 0 rows (empty Golden Standard)",
}

QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "tests" / "benchmark" / "questions.json"

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
    """Filters to the entries sharing one run_at, then — since resuming a
    partial/broken run under that same run_at (`make benchmark
    RUN_TIMESTAMP=... K="30 to 30"`) adds a second Allure result for
    whichever question(s) were retried, rather than replacing the first —
    keeps only the most recently executed entry per question (`story`),
    by Allure's own `stop` timestamp. `Path.glob()`'s enumeration order is
    filesystem-dependent, not chronological, so callers iterating entries
    as-is would otherwise silently keep whichever of the two glob happened
    to list last."""
    if not entries:
        return [], None
    if run_at is None:
        run_at = max(e["_parameters"].get("run_at", "") for e in entries)
    matching = [e for e in entries if e["_parameters"].get("run_at") == run_at]

    latest_by_story: dict[str, dict] = {}
    for entry in matching:
        story = entry["_labels"].get("story")
        current = latest_by_story.get(story)
        if current is None or entry.get("stop", 0) > current.get("stop", 0):
            latest_by_story[story] = entry
    return list(latest_by_story.values()), run_at


def _identity_str(record: dict) -> str | None:
    key = identity(record)
    return f"{key[0]}={key[1]}" if key else None


def _madro_identity_lists(allure_dir: Path, entries: list[dict]) -> dict[str, list[str]]:
    by_id = {}
    for entry in entries:
        question_id = entry["_labels"].get("story")
        ranked = _read_json_attachment(allure_dir, entry, "Ranked entities") or []
        ids = [_identity_str(e) for e in ranked]
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


def _latest_approach_per_alpha_beta(all_results: list[dict]) -> list[tuple[float, float, str]]:
    """Different sample sizes run at the same alpha/beta weighting are
    successive iterations of the same experiment, not variants meant to
    coexist — e.g. sample-10/-15/-25_alpha-0.7_beta-0.3 all landed here over
    time, but only the one with the most recent run is still valid. Keep one
    approach per (alpha, beta) pair: whichever has the latest run_at."""
    approaches_raw: dict[str, list[dict]] = {}
    for r in all_results:
        approach = r["_parameters"].get("approach")
        if approach:
            approaches_raw.setdefault(approach, []).append(r)

    best_by_pair: dict[tuple[float, float], tuple[str, str]] = {}
    for approach, entries in approaches_raw.items():
        m = re.search(r"alpha-([\d.]+)_beta-([\d.]+)", approach)
        if not m:
            continue
        _, run_at = _select_run(entries, None)
        key = (float(m.group(1)), float(m.group(2)))
        if key not in best_by_pair or (run_at or "") > best_by_pair[key][1]:
            best_by_pair[key] = (approach, run_at or "")

    return sorted(
        ((alpha, beta, approach) for (alpha, beta), (approach, _) in best_by_pair.items()),
        key=lambda t: -t[0],
    )


def build_comparison_grid(
    allure_dir: Path,
    all_results: list[dict],
    baseline_entries: list[dict],
    ground_truth_entries: list[dict],
    questions: list[dict],
) -> Workbook:
    """One row-block of GRID_ROWS rows per question, in questions.json order,
    with a blank separator row between blocks. Columns: question_id,
    question, "ground-truth", the given baseline model(s), and one column
    per distinct alpha/beta MADRO weighting found in allure-results (see
    _latest_approach_per_alpha_beta — superseded sample sizes at the same
    weighting are dropped), sorted by alpha descending. Identity values
    repeated within a question's block (across ground truth, baselines,
    and/or approaches) get a shared fill color, so overlaps are visible at a
    glance."""
    approaches_raw: dict[str, list[dict]] = {}
    for r in all_results:
        approach = r["_parameters"].get("approach")
        if approach:
            approaches_raw.setdefault(approach, []).append(r)

    approach_labels = _latest_approach_per_alpha_beta(all_results)

    ground_truth_selected, _ = _select_run(ground_truth_entries, None)
    ground_truth_ids = _identity_lists_from_rows_attachment(allure_dir, ground_truth_selected, "Ground Truth result")

    baseline_ids_by_model = _baseline_identity_lists_by_model(allure_dir, baseline_entries)
    baseline_model_keys = sorted(baseline_ids_by_model)
    approach_ids = {}
    for _, _, approach in approach_labels:
        entries, _ = _select_run(approaches_raw[approach], None)
        approach_ids[approach] = _madro_identity_lists(allure_dir, entries)

    question_text_by_id = {q["id"]: q.get("prompt", "") for q in questions}

    question_ids = set(ground_truth_ids)
    for ids_by_question in baseline_ids_by_model.values():
        question_ids |= set(ids_by_question)
    for ids_by_question in approach_ids.values():
        question_ids |= set(ids_by_question)
    ordered_ids = [q["id"] for q in questions if q["id"] in question_ids]
    ordered_ids += sorted(question_ids - set(ordered_ids), key=_question_sort_key)

    columns_meta = [("ground-truth", ground_truth_ids)]
    columns_meta += [(f"baseline-{model_key}", baseline_ids_by_model[model_key]) for model_key in baseline_model_keys]
    columns_meta += [
        (f"alpha-{alpha}_beta-{beta}", approach_ids[approach]) for alpha, beta, approach in approach_labels
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Comparison Grid"

    header_font = Font(bold=True)
    ws.cell(row=1, column=1, value="question_id").font = header_font
    ws.column_dimensions["A"].width = 12
    ws.cell(row=1, column=2, value="question").font = header_font
    ws.column_dimensions["B"].width = 45
    identity_col_start = 3
    for i, (name, _) in enumerate(columns_meta):
        col = identity_col_start + i
        ws.cell(row=1, column=col, value=name).font = header_font
        ws.column_dimensions[get_column_letter(col)].width = 26

    wrap_top = Alignment(wrap_text=True, vertical="top")

    row = 2
    for question_id in ordered_ids:
        block_start = row
        block_end = row + GRID_ROWS - 1

        ws.cell(row=block_start, column=1, value=question_id)
        ws.merge_cells(start_row=block_start, start_column=1, end_row=block_end, end_column=1)
        ws.cell(row=block_start, column=1).alignment = wrap_top

        ws.cell(row=block_start, column=2, value=question_text_by_id.get(question_id, ""))
        ws.merge_cells(start_row=block_start, start_column=2, end_row=block_end, end_column=2)
        ws.cell(row=block_start, column=2).alignment = wrap_top

        block_values: dict[tuple[int, int], str] = {}
        for i, (_, ids_by_question) in enumerate(columns_meta):
            col = identity_col_start + i
            ids = ids_by_question.get(question_id, [])
            for r_offset in range(GRID_ROWS):
                value = ids[r_offset] if r_offset < len(ids) else ""
                r = block_start + r_offset
                ws.cell(row=r, column=col, value=value).alignment = wrap_top
                if value:
                    block_values[(r, col)] = value

        counts: dict[str, int] = {}
        for value in block_values.values():
            counts[value] = counts.get(value, 0) + 1
        color_by_value = {
            value: GRID_PALETTE[i % len(GRID_PALETTE)]
            for i, value in enumerate(v for v, c in sorted(counts.items()) if c >= 2)
        }
        for (r, c), value in block_values.items():
            if value in color_by_value:
                color = color_by_value[value]
                ws.cell(row=r, column=c).fill = PatternFill(start_color=color, end_color=color, fill_type="solid")

        row = block_end + 2  # blank separator row between question blocks

    ws.freeze_panes = "C2"
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
        madro_by_id[question_id] = ranked

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
    excluded_present = sorted(all_ids & set(EXCLUDED_QUESTIONS), key=_question_sort_key)
    if excluded_present:
        print("\nExcluded from precision/recall:")
        for question_id in excluded_present:
            print(f"  {question_id}: {EXCLUDED_QUESTIONS[question_id]}")
    all_ids -= set(EXCLUDED_QUESTIONS)

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

    questions = json.loads(QUESTIONS_PATH.read_text())
    grid = build_comparison_grid(args.allure_dir, all_results, baseline_candidates, ground_truth_all, questions)
    grid.save(args.grid_out)
    print(f"Wrote {args.grid_out}")


if __name__ == "__main__":
    main()
