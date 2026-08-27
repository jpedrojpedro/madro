## One-time environment check
- Check if `madro_db` and `dowser_db` containers up
```bash
docker compose up -d
```
- Ollama running locally with `qwen2.5vl:7b`, `qwen2.5-coder:7b`, `llama3.1:8b` pulled
- `.env` has `GOOGLE_API_KEY`, `DATABASE_URL`, `RETRIEVAL_DB_URL` set

## Ground Truth — run once, first (Baseline's identity hint depends on it)
- Trigger `Makefile` command
```bash
make benchmark-ground-truth
```
_Optional_: sanity-check on a few questions first with make benchmark-ground-truth K="1 to 5".
Then inspect with allure serve allure-results — open the GroundTruth_gemini @ ... suite and spot-check a handful of generated SQL queries before moving on
if something looks systematically wrong here, it'll poison everything downstream.

## Baseline — run once (it doesn't vary by alpha/beta)
- Trigger `Makefile` command
```bash
make benchmark-baseline
```
  - It runs Gemini + Qwen2.5-Coder + Llama 3.1 × 50 questions = 150 tests
  - now steered by the identity hints Ground Truth just produced.

## MADRO — one run per alpha/beta combo
- Trigger `Makefile` command
```bash
make benchmark SAMPLE=10 FUSION_LEX=0.7 FUSION_SEM=0.3
make benchmark SAMPLE=10 FUSION_LEX=0.5 FUSION_SEM=0.5
make benchmark SAMPLE=10 FUSION_LEX=0.3 FUSION_SEM=0.7
```
  - I used SAMPLE=10 because that's what your prior runs of these exact three alpha/beta combos used (I found them already in allure-results/, from your earlier benchmarking) — override if you want a different sample size this time. Each run gets its own approach label (sample-10_alpha-0.7_beta-0.3, etc.) and a fresh timestamp, so re-running the same combo later just adds another run rather than overwriting.

## Compare each MADRO run against Ground Truth + Baseline
- Trigger `Makefile` command
```bash
make compare-baseline APPROACH=sample-10_alpha-0.7_beta-0.3 OUT=comparison_70_30.json GRID_OUT=comparison_70_30.xlsx
make compare-baseline APPROACH=sample-10_alpha-0.5_beta-0.5 OUT=comparison_50_50.json GRID_OUT=comparison_50_50.xlsx
make compare-baseline APPROACH=sample-10_alpha-0.3_beta-0.7 OUT=comparison_30_70.json GRID_OUT=comparison_30_70.xlsx
```
Give each a distinct OUT/GRID_OUT — otherwise the second and third calls silently overwrite the first's comparison_results.json/comparison_grid.xlsx. Each prints two tables (Baseline vs Ground Truth, MADRO vs Ground Truth) with precision/recall@1/5/10 and an identity-match rate, then writes the JSON + spreadsheet.

> Check the three comparison_* outputs and the Allure report;
> once you're happy, commit/push the Makefile addition (and anything else) yourself,
> per your General Instruction.
