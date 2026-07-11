#!/usr/bin/env python
"""
Deletes every Allure result + attachment file belonging to one suite,
matched by its exact `parentSuite` label. Dry-run by default — only lists
what would be deleted; pass --yes to actually delete.

Refuses to delete an attachment that's also referenced by a result outside
the target suite, and warns (without deleting) if any container file
references one of the target suite's test UUIDs.

Usage:
    poetry run python scripts/delete_allure_suite.py --suite "sample-10_alpha-0.5_beta-0.5 @ 2026-07-07T04:08:32Z"
    poetry run python scripts/delete_allure_suite.py --suite "baseline" --yes
"""

import argparse
import json
import sys
from pathlib import Path


def _load_results(allure_dir: Path) -> list[dict]:
    results = []
    for path in allure_dir.glob("*-result.json"):
        data = json.loads(path.read_text())
        data["_path"] = path
        data["_labels"] = {label["name"]: label["value"] for label in data.get("labels", [])}
        results.append(data)
    return results


def _attachment_sources(result: dict) -> set[str]:
    sources = {a["source"] for a in result.get("attachments", [])}
    for step in result.get("steps", []):
        sources |= {a["source"] for a in step.get("attachments", [])}
    return sources


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suite", required=True, help="Exact parentSuite label to delete, e.g. 'sample-10_alpha-0.5_beta-0.5 @ 2026-07-07T04:08:32Z'")
    parser.add_argument("--allure-dir", default="allure-results", type=Path)
    parser.add_argument("--yes", action="store_true", help="Actually delete. Without this, only lists what would be deleted.")
    args = parser.parse_args()

    all_results = _load_results(args.allure_dir)
    matching = [r for r in all_results if r["_labels"].get("parentSuite") == args.suite]

    if not matching:
        print(f"No results found with parentSuite={args.suite!r} in {args.allure_dir}", file=sys.stderr)
        sys.exit(1)

    matching_attachments: set[str] = set()
    for r in matching:
        matching_attachments |= _attachment_sources(r)

    other_results = [r for r in all_results if r["_labels"].get("parentSuite") != args.suite]
    shared: dict[str, set[str]] = {}
    for r in other_results:
        overlap = matching_attachments & _attachment_sources(r)
        if overlap:
            shared[r["_path"].name] = overlap

    protected = {source for sources in shared.values() for source in sources}
    deletable_attachments = matching_attachments - protected

    matching_uuids = {r["_path"].stem.removesuffix("-result") for r in matching}
    container_hits: dict[str, set[str]] = {}
    for path in args.allure_dir.glob("*-container.json"):
        container = json.loads(path.read_text())
        hit = set(container.get("children", [])) & matching_uuids
        if hit:
            container_hits[path.name] = hit

    result_files = [r["_path"] for r in matching]
    attachment_files = [args.allure_dir / source for source in deletable_attachments]
    stories = sorted({r["_labels"].get("story") for r in matching})

    print(f"Suite: {args.suite}")
    print(f"Questions ({len(stories)}): {', '.join(stories)}")
    print(f"Result files: {len(result_files)}")
    print(f"Attachment files: {len(attachment_files)}" + (f" ({len(protected)} excluded — shared with other suites)" if protected else ""))
    print(f"Total to delete: {len(result_files) + len(attachment_files)}")

    if shared:
        print("\nWARNING — attachments shared with other suites, will NOT be deleted:")
        for name, sources in shared.items():
            print(f"  referenced by {name}: {sorted(sources)}")

    if container_hits:
        print("\nWARNING — container file(s) reference this suite's tests (left as-is):")
        for name, hit in container_hits.items():
            print(f"  {name} -> {sorted(hit)}")

    if not args.yes:
        print("\nDry run only — nothing deleted. Re-run with --yes to actually delete.")
        return

    deleted = 0
    for path in result_files + attachment_files:
        if path.exists():
            path.unlink()
            deleted += 1
    print(f"\nDeleted {deleted} files.")


if __name__ == "__main__":
    main()
