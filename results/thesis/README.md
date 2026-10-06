# Thesis results (Chapter 6)

Frozen snapshot of the benchmark comparison reported in Chapter 6 of the thesis
*MADRO: Multi-Agent Retrieval over Heterogeneous Multimodal Data* (PUC-Rio, 2026).
Released under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).

## Source runs

All runs were executed on 2026-08-28/29 over the 50 questions in
`tests/benchmark/questions.json`, MADRO with `sampling_depth=25`.

| Suite | `run_at` (UTC) |
|---|---|
| Ground Truth (gemini) | 2026-08-28T21:25:17Z |
| Baseline (gemini, qwen2.5-coder, llama3.1) | 2026-08-28T21:38:30Z |
| MADRO α=0.7 / β=0.3 | 2026-08-28T22:22:56Z |
| MADRO α=0.5 / β=0.5 | 2026-08-28T23:36:45Z |
| MADRO α=0.3 / β=0.7 | 2026-08-29T00:12:10Z |

## Files

| File | Contents |
|---|---|
| `comparison_alpha-<α>_beta-<β>.json` | MADRO at that fusion weight and Baseline (gemini), each scored against Ground Truth — aggregate and per-question P@k / R@k, plus the ranked identities behind them |
| `comparison_baseline-<model>.json` | Same, with the Baseline side swapped for `qwen2.5-coder` or `llama3.1` (MADRO side is α=0.7 / β=0.3) |
| `comparison_grid.xlsx` | Ranked identities per question for Ground Truth, Baseline (gemini), and all three MADRO runs, side by side |

Rows are identified by opaque database keys (`profile_id=…`, `publication_id=…`); the
underlying corpus will be released separately.

## Scoring

Scored with the code at tag [`thesis-results`](https://github.com/jpedrojpedro/madro/tree/thesis-results) (commit `5d9748f`).
The scoring changed after the thesis defense, so `main` does **not** reproduce these numbers.

Averages in the JSON files are over the questions each tool produced an answer for (`n`).
The thesis reports them over all 50 questions, counting unanswered ones as zero — i.e.
`thesis value = precision_mean × n / 50`. For example, Baseline (gemini) P@1 is
`0.265 × 49 / 50 = 0.260`.

## Reproducing

```bash
git checkout thesis-results
poetry run python scripts/compare_baseline.py \
  --approach sample-25_alpha-0.7_beta-0.3 \
  --allure-dir <allure-results directory> \
  --out comparison.json --grid-out comparison_grid.xlsx
```

The raw Allure results (which embed retrieved corpus content) are not published yet.
