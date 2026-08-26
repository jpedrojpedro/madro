"""
Compares MADRO's ranked entities against the naive SQL baseline's rows.

Both sides are plain, already rank-ordered `list[dict]` — either live
in-process objects (`RankedEntity.entity_data`, `NaiveSQLOutcome.rows`) or
records parsed back out of Allure JSON attachments — so `compare()` doesn't
care where its inputs came from.
"""

ID_FIELD_PRIORITY = ("profile_id", "publication_id", "comment_id", "follower_profile_id")
RANKS = (1, 5, 10)

# test_baseline.py labels each baseline result's Allure parent_suite as
# f"{BASELINE_SUITE_PREFIX}{model_key} @ {timestamp}" — the same
# "{label} @ {timestamp}" convention test_benchmark.py uses for its own
# RUN_ID, so baseline runs show up per-model/per-run just like MADRO
# approach runs do. Consumers distinguish baseline entries from MADRO ones
# by this prefix rather than an exact "baseline" match.
BASELINE_SUITE_PREFIX = "Baseline_"

# Same convention as BASELINE_SUITE_PREFIX, for test_ground_truth.py — Ground
# Truth only ever runs against Gemini, but keeps the "{label} @ {timestamp}"
# shape so it can be picked out of allure-results/ the same way.
GROUND_TRUTH_SUITE_PREFIX = "GroundTruth_"


def identity(record: dict) -> tuple[str, str] | None:
    """First matching (field, value) pair from ID_FIELD_PRIORITY, or None if
    the record carries none of them — mirrors the join-key convention every
    retrieval agent and EntityResolver already use."""
    for field_name in ID_FIELD_PRIORITY:
        value = record.get(field_name)
        if value is not None:
            return (field_name, str(value))
    return None


def _ids(records: list[dict]) -> tuple[list[tuple[str, str]], int]:
    ids: list[tuple[str, str]] = []
    dropped = 0
    for record in records:
        key = identity(record)
        if key is None:
            dropped += 1
            continue
        ids.append(key)
    return ids, dropped


def compare(madro_records: list[dict], baseline_records: list[dict]) -> dict:
    """MADRO's ranked list is treated as the reference ("relevant") set,
    the baseline's rows as retrieved — precision@k is the fraction of the
    baseline's top-k also present in MADRO's top-k, recall@k the fraction
    of MADRO's top-k the baseline also retrieved. The two diverge when the
    lists have different lengths (e.g. MADRO resolves fewer than k distinct
    entities while the baseline always returns up to 10)."""
    madro_ids, madro_dropped = _ids(madro_records)
    baseline_ids, baseline_dropped = _ids(baseline_records)

    metrics = {}
    for k in RANKS:
        topk_madro = madro_ids[:k]
        topk_baseline = baseline_ids[:k]
        overlap = set(topk_madro) & set(topk_baseline)
        metrics[k] = {
            "precision": len(overlap) / len(topk_baseline) if topk_baseline else 0.0,
            "recall": len(overlap) / len(topk_madro) if topk_madro else 0.0,
            "overlap": len(overlap),
            "madro_count": len(topk_madro),
            "baseline_count": len(topk_baseline),
        }

    positions_madro = {key: i + 1 for i, key in enumerate(madro_ids)}
    positions_baseline = {key: i + 1 for i, key in enumerate(baseline_ids)}
    diff = sorted(
        (
            {
                "identity": f"{key[0]}={key[1]}",
                "madro_position": positions_madro.get(key),
                "baseline_position": positions_baseline.get(key),
            }
            for key in set(positions_madro) | set(positions_baseline)
        ),
        key=lambda d: (d["madro_position"] is None, d["madro_position"] or 0, d["baseline_position"] or 0),
    )

    return {
        "metrics": metrics,
        "diff": diff,
        "madro_dropped_no_identity": madro_dropped,
        "baseline_dropped_no_identity": baseline_dropped,
    }
