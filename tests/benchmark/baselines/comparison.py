"""
Compares a retrieved result set (Baseline's rows or MADRO's ranked entities)
against Ground Truth's rows, which stands as the reference ("relevant") set
for both — see docs/adr/0001-ground-truth-is-the-benchmark-reference.md.

All three sides are plain, already rank-ordered `list[dict]` — either live
in-process objects (`NaiveSQLOutcome.rows`) or records parsed back out of
Allure JSON attachments — so `compare()` doesn't care where its inputs came
from, EXCEPT: MADRO's side must be passed the full `{"entity_id":
"kind:value", "entity_data": {...}}` records straight from the "Ranked
entities" attachment (or `RankedEntity` instances' own shape), not just
`entity_data` unwrapped — see `identity()` for why.
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
    """(field, value) identifying this record's entity, or None if it can't
    be identified at all.

    A MADRO "Ranked entities" record (`{"entity_id": "kind:value",
    "entity_data": {...}}`) already carries the identity EntityResolver
    actually resolved it under — trust that instead of guessing from
    ID_FIELD_PRIORITY against `entity_data`. A record's raw data can
    legitimately carry more than one ID field (e.g. a comment record also
    carries its parent's publication_id, needed for EntityResolver's own join
    key — see aggregation/entity_resolver.py), so scanning `entity_data` for
    "whichever ID field is present first" would silently prefer the wrong
    one whenever both are there. `entity_id` also distinguishes a real
    identity ("comment:123") from EntityResolver's `artifact_id:idx` fallback
    for a record with no identity at all (see EntityResolver._entity_id) —
    that fallback has no real kind and must report None here too, not borrow
    whatever ID field happens to still be sitting in entity_data.

    A flat Ground Truth/Baseline SQL row has no such wrapper and is never
    ambiguous the same way (each query selects exactly the one relevant id
    column), so it still goes through the ID_FIELD_PRIORITY scan."""
    if "entity_id" in record and "entity_data" in record:
        kind, _, value = record["entity_id"].partition(":")
        field_name = f"{kind}_id"
        return (field_name, value) if field_name in ID_FIELD_PRIORITY else None

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


def compare(reference_records: list[dict], retrieved_records: list[dict]) -> dict:
    """`reference_records` (Ground Truth's resolved rows) is the "relevant"
    set; `retrieved_records` (Baseline's rows or MADRO's ranked entities) is
    scored against it — precision@k is the fraction of retrieved's top-k
    also present in reference's top-k, recall@k the fraction of reference's
    top-k that retrieved also found. The two diverge when the lists have
    different lengths (e.g. Ground Truth resolves fewer than k distinct
    entities while Baseline always returns up to 10).

    Also reports whether the two sides even agree on *what kind* of entity
    they're returning (`identity_match`) — a real 0% overlap on the wrong
    entity type isn't a ranking failure, it's a granularity mismatch, and
    the two shouldn't be conflated."""
    reference_ids, reference_dropped = _ids(reference_records)
    retrieved_ids, retrieved_dropped = _ids(retrieved_records)

    metrics = {}
    for k in RANKS:
        topk_reference = reference_ids[:k]
        topk_retrieved = retrieved_ids[:k]
        overlap = set(topk_reference) & set(topk_retrieved)
        metrics[k] = {
            "precision": len(overlap) / len(topk_retrieved) if topk_retrieved else 0.0,
            "recall": len(overlap) / len(topk_reference) if topk_reference else 0.0,
            "overlap": len(overlap),
            "reference_count": len(topk_reference),
            "retrieved_count": len(topk_retrieved),
        }

    positions_reference = {key: i + 1 for i, key in enumerate(reference_ids)}
    positions_retrieved = {key: i + 1 for i, key in enumerate(retrieved_ids)}
    diff = sorted(
        (
            {
                "identity": f"{key[0]}={key[1]}",
                "reference_position": positions_reference.get(key),
                "retrieved_position": positions_retrieved.get(key),
            }
            for key in set(positions_reference) | set(positions_retrieved)
        ),
        key=lambda d: (
            d["reference_position"] is None,
            d["reference_position"] or 0,
            d["retrieved_position"] or 0,
        ),
    )

    reference_identity_field = reference_ids[0][0] if reference_ids else None
    retrieved_identity_field = retrieved_ids[0][0] if retrieved_ids else None
    identity_match = (
        reference_identity_field == retrieved_identity_field
        if reference_identity_field and retrieved_identity_field
        else None
    )

    return {
        "metrics": metrics,
        "diff": diff,
        "reference_dropped_no_identity": reference_dropped,
        "retrieved_dropped_no_identity": retrieved_dropped,
        "reference_identity_field": reference_identity_field,
        "retrieved_identity_field": retrieved_identity_field,
        "identity_match": identity_match,
    }
