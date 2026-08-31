#!/usr/bin/env python
"""
Generates the Benchmark Execution Appendix — a standalone, thesis-styled
LaTeX/PDF companion document with one page per benchmark question, showing
that question's full MADRO pipeline execution trace (sub-demands, published
jobs, per-agent generated SQL, top-ranked entities, synthesized answer) for a
given MADRO run, already sitting in allure-results/ (from `make benchmark`).
See docs/adr/0006-benchmark-execution-appendix-is-a-standalone-trace-not-an-evaluation-artifact.md.

Two outputs per selected question, under --out-dir:
  data/<id>.json  -- stable extraction of the 5-stage trace (checked into
                     git, so the appendix survives allure-results/ being
                     pruned/regenerated)
  pages/<id>.tex  -- the rendered one-page LaTeX fragment (\\input by
                     main.tex), regenerated from data/<id>.json

Mirrors the allure-results parsing conventions of export_madro_xlsx.py.

Usage:
    poetry run python scripts/generate_benchmark_appendix.py
    poetry run python scripts/generate_benchmark_appendix.py --question-ids Q07
    poetry run python scripts/generate_benchmark_appendix.py --approach sample-25_alpha-0.7_beta-0.3 --top-n 5
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from markdown_it import MarkdownIt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MARKDOWN = MarkdownIt("commonmark")

QUESTIONS_PATH = Path(__file__).resolve().parent.parent / "tests" / "benchmark" / "questions.json"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "benchmark-appendix"

STAGE_ATTACHMENTS = {
    "Run thread": "Sub-demands",
    "Job queue": "Published jobs",
    "Agent runner": "Artifacts",
    "Relevance ranking": "Ranked entities",
    "Response synthesis": "Synthesized answer",
}


# --- allure-results parsing (mirrors export_madro_xlsx.py) -----------------

def _load_results(allure_dir: Path) -> list[dict]:
    results = []
    for path in allure_dir.glob("*-result.json"):
        data = json.loads(path.read_text())
        data["_labels"] = {label["name"]: label["value"] for label in data.get("labels", [])}
        data["_parameters"] = {p["name"]: _unwrap(p["value"]) for p in data.get("parameters", [])}
        results.append(data)
    return results


def _unwrap(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _find_attachment(result: dict, name: str) -> dict | None:
    for attachment in result.get("attachments", []):
        if attachment["name"] == name:
            return attachment
    for step in result.get("steps", []):
        for attachment in step.get("attachments", []):
            if attachment["name"] == name:
                return attachment
    return None


def _read_attachment(allure_dir: Path, result: dict, name: str) -> str | None:
    attachment = _find_attachment(result, name)
    if attachment is None:
        return None
    return (allure_dir / attachment["source"]).read_text()


def _select_run(all_results: list[dict], approach: str | None, run_at: str | None) -> tuple[str, str, dict[str, dict]]:
    madro_all = [
        r for r in all_results
        if r["_parameters"].get("approach") and r.get("fullName") == "tests.benchmark.test_benchmark#test_benchmark_question"
    ]
    if not madro_all:
        print("No MADRO benchmark results found — run `make benchmark` first", file=sys.stderr)
        sys.exit(1)

    if approach is None:
        approach = max(madro_all, key=lambda r: r["_parameters"].get("run_at", ""))["_parameters"]["approach"]
    candidates = [r for r in madro_all if r["_parameters"].get("approach") == approach]
    if not candidates:
        print(f"No MADRO results found for approach={approach!r}", file=sys.stderr)
        sys.exit(1)

    if run_at is None:
        run_at = max(e["_parameters"].get("run_at", "") for e in candidates)
    entries = [e for e in candidates if e["_parameters"].get("run_at") == run_at]

    # A question can appear more than once for the same run (e.g. one broken
    # attempt, one passed retry) -- keep the latest by `stop`, preferring
    # "passed" over any other status.
    by_question_id: dict[str, dict] = {}
    for entry in entries:
        qid = entry["_labels"].get("story")
        current = by_question_id.get(qid)
        if current is None:
            by_question_id[qid] = entry
            continue
        current_rank = (current.get("status") == "passed", current.get("stop", 0))
        entry_rank = (entry.get("status") == "passed", entry.get("stop", 0))
        if entry_rank > current_rank:
            by_question_id[qid] = entry

    return approach, run_at, by_question_id


# --- extraction --------------------------------------------------------------

def _extract_question(allure_dir: Path, question: dict, entry: dict, approach: str, run_at: str) -> dict:
    if entry.get("status") != "passed":
        raise ValueError(f"{question['id']} run did not pass (status={entry.get('status')})")

    sub_demands_raw = _read_attachment(allure_dir, entry, STAGE_ATTACHMENTS["Run thread"])
    jobs_raw = _read_attachment(allure_dir, entry, STAGE_ATTACHMENTS["Job queue"])
    artifacts_raw = _read_attachment(allure_dir, entry, STAGE_ATTACHMENTS["Agent runner"])
    ranked_raw = _read_attachment(allure_dir, entry, STAGE_ATTACHMENTS["Relevance ranking"])
    answer = _read_attachment(allure_dir, entry, STAGE_ATTACHMENTS["Response synthesis"]) or ""

    return {
        "id": question["id"],
        "prompt": question["prompt"],
        "complexity": question["complexity"],
        "hint": question.get("hint", []),
        "approach": approach,
        "run_at": run_at,
        "sub_demands": json.loads(sub_demands_raw) if sub_demands_raw else [],
        "jobs": json.loads(jobs_raw) if jobs_raw else [],
        "artifacts": json.loads(artifacts_raw) if artifacts_raw else [],
        "ranked_entities": json.loads(ranked_raw) if ranked_raw else [],
        "synthesized_answer": answer.strip(),
    }


# --- LaTeX rendering ----------------------------------------------------------

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "^": r"\textasciicircum{}",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "%": r"\%",
}
_LATEX_SPECIAL_RE = re.compile("|".join(re.escape(c) for c in _LATEX_SPECIAL))


def _strip_non_latin(text: str) -> str:
    """Drop emoji/pictographic characters lmodern (pdflatex) can't render, keeping
    Latin letters (incl. accented Portuguese), digits, and standard punctuation."""
    out = []
    for ch in text:
        if ch in "\n\r\t":
            out.append(" ")
            continue
        category = unicodedata.category(ch)
        if category.startswith(("L", "N", "P", "Z")) or ch in "\\{}$&#^_~%@":
            out.append(ch)
    return "".join(out)


def tex_escape(text: str) -> str:
    text = _strip_non_latin(text or "")
    text = _LATEX_SPECIAL_RE.sub(lambda m: _LATEX_SPECIAL[m.group()], text)
    # Collapse repeated whitespace, but never trim leading/trailing spaces --
    # this is called on markdown-split fragments (see _inline_markdown) where
    # a trailing/leading space is the only thing separating a word from a
    # \textbf{} span that follows/precedes it.
    return re.sub(r" {2,}", " ", text)


def _render_inline(children) -> str:
    """Render one markdown-it 'inline' token's children (text, **bold**,
    *italic*, `code`, line breaks) as LaTeX."""
    out = []
    for tok in children or []:
        if tok.type == "text":
            out.append(tex_escape(tok.content))
        elif tok.type == "code_inline":
            out.append(rf"\texttt{{{tex_escape(tok.content)}}}")
        elif tok.type == "strong_open":
            out.append(r"\textbf{")
        elif tok.type == "strong_close":
            out.append("}")
        elif tok.type == "em_open":
            out.append(r"\textit{")
        elif tok.type == "em_close":
            out.append("}")
        elif tok.type in ("softbreak", "hardbreak"):
            out.append(" ")
        elif tok.type in ("link_open", "link_close"):
            pass  # keep the link text, drop the markup
        elif tok.content:
            out.append(tex_escape(tok.content))
    return "".join(out)


def markdown_to_tex(text: str) -> str:
    """Render MADRO's Markdown synthesized answer as formatted LaTeX (bold,
    headings, lists) via markdown-it-py's CommonMark token stream, rather than
    displaying the raw Markdown/HTML notation verbatim."""
    out: list[str] = []
    for tok in _MARKDOWN.parse(text or ""):
        if tok.type == "heading_open":
            out.append(r"\par\noindent\textbf{")
        elif tok.type == "heading_close":
            out.append(r"}\par")
        elif tok.type == "paragraph_close":
            out.append(r"\par")
        elif tok.type == "bullet_list_open":
            # Plain itemize, no enumitem brackets: thesispuc.cls hooks
            # \AfterBegin{itemize}{\addtolength{\itemsep}{-0.5em}} (line 698),
            # which breaks when the itemize it fires on is an
            # enumitem-customized one nested inside a minipage (this list
            # sits inside the stage-5 answer box) -- "Something's wrong--
            # perhaps a missing \item." Confirmed by isolated reproduction;
            # plain itemize avoids the conflict entirely. enumerate isn't
            # hooked by the class, so ordered lists keep their brackets.
            out.append(r"\begin{itemize}")
        elif tok.type == "bullet_list_close":
            out.append(r"\end{itemize}")
        elif tok.type == "ordered_list_open":
            out.append(r"\begin{enumerate}[nosep,leftmargin=1.2em]")
        elif tok.type == "ordered_list_close":
            out.append(r"\end{enumerate}")
        elif tok.type == "list_item_open":
            out.append(r"\item ")
        elif tok.type == "hr":
            out.append(r"\par\hrule\par")
        elif tok.type == "blockquote_open":
            out.append(r"\textit{")
        elif tok.type == "blockquote_close":
            out.append(r"}\par")
        elif tok.type in ("fence", "code_block"):
            out.append(rf"\texttt{{{tex_escape(tok.content)}}}\par")
        elif tok.type == "inline":
            out.append(_render_inline(tok.children))
    # "\n", not "" -- a bare "\par" immediately followed by text with no
    # separator (e.g. "\parHowever") gets parsed as one undefined control
    # word, since LaTeX control words greedily consume following letters.
    return "\n".join(out)


_STRAIGHT_QUOTE_PAIR_RE = re.compile(r'"([^"]*)"')


def _smart_quotes(text: str) -> str:
    """Replace paired straight double quotes with LaTeX's `` / '' ligature,
    which renders as proper curly quotes instead of the straight glyph."""
    return _STRAIGHT_QUOTE_PAIR_RE.sub(r"``\1''", text)


def truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _entity_label(entity: dict) -> str:
    kind, _, ident = entity["entity_id"].partition(":")
    data = entity.get("entity_data", {})
    if kind == "profile":
        return f"profile: @{data.get('username', ident)}"
    if kind == "publication":
        return f"publication: {ident}"
    return f"{kind}: {ident}"


def _entity_snippet(entity: dict, limit: int = 90) -> str:
    data = entity.get("entity_data", {})
    text = data.get("biography") or data.get("description") or ""
    return tex_escape(truncate(text, limit))


def render_page(q: dict, top_n: int) -> str:
    lines: list[str] = []

    lines.append(rf"\subsection*{{{tex_escape(q['id'])} \quad {tex_escape(q['complexity'])}}}")
    lines.append(rf"\textbf{{Prompt:}} \textit{{{tex_escape(q['prompt'])}}}")
    lines.append(r"\par")
    lines.append(r"\vspace{2pt}\hrule\vspace{3pt}")
    lines.append(r"\begingroup\footnotesize")

    # Stage 1 -- Run thread
    lines.append(r"\noindent\textbf{1. Run thread --- sub-demands}\par")
    if q["sub_demands"]:
        lines.append(r"\begin{enumerate}[label=\alph*),nosep,leftmargin=1.5em]")
        for sd in q["sub_demands"]:
            lines.append(rf"  \item \textit{{{tex_escape(sd.get('topic', ''))}}}: {tex_escape(sd.get('demand', ''))}")
        lines.append(r"\end{enumerate}")
    else:
        lines.append(r"\textit{(none)}\par")

    # Stage 2 -- Job queue
    lines.append(r"\noindent\textbf{2. Job queue --- published jobs}\par")
    if q["jobs"]:
        agent_list = ", ".join(tex_escape(j.get("agent", "")) for j in q["jobs"])
        lines.append(rf"{len(q['jobs'])} job(s) published: {agent_list}.\par")
    else:
        lines.append(r"\textit{(none)}\par")

    # Stage 3 -- Agent runner
    lines.append(r"\noindent\textbf{3. Agent runner --- generated SQL per agent}\par")
    if q["artifacts"]:
        for artifact in q["artifacts"]:
            agent = tex_escape(artifact.get("agent", ""))
            status = tex_escape(artifact.get("status", ""))
            # Reflow to one dense line -- lstlisting's breaklines wraps it to
            # whatever the host column width actually is, which packs far
            # fewer visual lines than the LLM's one-clause-per-line SQL style.
            # Whitespace-only change: the query text itself is untouched.
            sql = " ".join(artifact.get("provenance", {}).get("generated_sql", "").split())
            usage = artifact.get("provenance", {}).get("usage", {})
            attempts = artifact.get("provenance", {}).get("sql_attempts", "?")
            lines.append(rf"\textit{{{agent}}} ({status}):\par")
            if sql:
                lines.append(r"\begin{lstlisting}[language=SQL,basicstyle=\ttfamily\tiny,frame=single,aboveskip=1pt,belowskip=1pt,framesep=2pt]")
                lines.append(sql)
                lines.append(r"\end{lstlisting}")
            lines.append(
                rf"sql\_attempts={attempts}, "
                rf"tokens: in={usage.get('input_tokens', '?')} "
                rf"out={usage.get('output_tokens', '?')} "
                rf"total={usage.get('total_tokens', '?')}\par"
            )
    else:
        lines.append(r"\textit{(none)}\par")

    # Stage 4 -- Relevance ranking
    total = len(q["ranked_entities"])
    shown = sorted(q["ranked_entities"], key=lambda e: e.get("s_relevance", 0), reverse=True)[:top_n]
    lines.append(rf"\noindent\textbf{{4. Relevance ranking --- top {len(shown)} of {total} ranked entities}}\par")
    if shown:
        lines.append(r"\begingroup\scriptsize\setlength{\tabcolsep}{3pt}\renewcommand{\arraystretch}{0.9}")
        lines.append(r"\begin{tabular}{@{}p{3.4cm}rrrp{5.6cm}@{}}")
        lines.append(r"\toprule")
        lines.append(r"Entity & $S_{\mathrm{lex}}$ & $S_{\mathrm{sem}}$ & $S_{\mathrm{rel}}$ & Snippet \\")
        lines.append(r"\midrule")
        for e in shown:
            lines.append(
                rf"{tex_escape(_entity_label(e))} & "
                rf"{e.get('s_lex', 0):.3f} & {e.get('s_sem', 0):.3f} & {e.get('s_relevance', 0):.3f} & "
                rf"{_entity_snippet(e, limit=75)} \\"
            )
        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
        lines.append(r"\endgroup")
        lines.append(r"\par")  # tabular doesn't end its own paragraph -- without
        # this, the next heading gets glued onto the table's last line.
    else:
        lines.append(r"\textit{(none)}\par")

    # Stage 5 -- Response synthesis. MADRO's raw output is Markdown; rendered
    # as formatted LaTeX (bold, headings, lists) via markdown-it-py's
    # CommonMark parser, rather than displaying raw `**`/`###`/`*` notation.
    lines.append(r"\noindent\textbf{5. Response synthesis --- synthesized answer}\par")
    answer = _smart_quotes(markdown_to_tex(q["synthesized_answer"]))
    # Framed/shaded like the SQL listings above (same `backcolour`), so the
    # final answer stands out the same way -- but a color box, not a
    # verbatim/lstlisting: this content is formatted LaTeX (\textbf, itemize),
    # which a literal verbatim environment would print as raw source again.
    lines.append(r"{\setlength{\fboxsep}{4pt}\setlength{\fboxrule}{0.4pt}\noindent\fcolorbox{black}{backcolour}{\begin{minipage}{\dimexpr\linewidth-2\fboxsep-2\fboxrule\relax}")
    lines.append(answer if answer.strip() else r"\textit{(empty)}")
    lines.append(r"\end{minipage}}}")

    lines.append(r"\endgroup")
    lines.append(r"\clearpage")
    return "\n".join(lines) + "\n"


# --- main ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allure-dir", default="allure-results", type=Path)
    parser.add_argument("--approach", default="sample-25_alpha-0.7_beta-0.3", help="MADRO run label (default: the 70/30 run)")
    parser.add_argument("--run-at", default=None, help="Disambiguate if --approach was run more than once (default: most recent)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, type=Path)
    parser.add_argument("--top-n", default=5, type=int, help="Ranked entities shown per page (default: 5)")
    parser.add_argument("--question-ids", nargs="*", default=None, help="Restrict to these question IDs (default: all 50)")
    args = parser.parse_args()

    questions = json.loads(QUESTIONS_PATH.read_text())
    if args.question_ids:
        wanted = set(args.question_ids)
        questions = [q for q in questions if q["id"] in wanted]

    all_results = _load_results(args.allure_dir)
    approach, run_at, by_question_id = _select_run(all_results, args.approach, args.run_at)
    print(f"Using MADRO approach={approach!r} run_at={run_at}")

    data_dir = args.out_dir / "data"
    pages_dir = args.out_dir / "pages"
    data_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    missing, failed = [], []
    generated = []
    for question in questions:
        entry = by_question_id.get(question["id"])
        if entry is None:
            missing.append(question["id"])
            continue
        try:
            extracted = _extract_question(args.allure_dir, question, entry, approach, run_at)
        except ValueError as exc:
            failed.append(f"{question['id']}: {exc}")
            continue

        data_path = data_dir / f"{question['id']}.json"
        data_path.write_text(json.dumps(extracted, indent=2, ensure_ascii=False) + "\n")

        page_path = pages_dir / f"{question['id']}.tex"
        page_path.write_text(render_page(extracted, args.top_n))
        generated.append(question["id"])

    print(f"Generated {len(generated)} page(s): {', '.join(generated)}")
    if missing:
        print(f"No MADRO result for: {', '.join(missing)}", file=sys.stderr)
    if failed:
        print(f"Failed: {'; '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
