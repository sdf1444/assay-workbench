# Assay Workbench

Assay Workbench is a personal project that simulates the first review of a 96-well inhibition screening plate. A scientist imports a plate reader CSV, checks whether controls behaved as expected, inspects sample wells, then exports the annotated measurements. The included plates are synthetic.

## Run locally

Requires Python 3.11+ and Node.js 22+. Run these commands from the extracted `assay-workbench` directory.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
python -m uvicorn backend.main:app
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. Open http://127.0.0.1:8000 for the workbench and http://127.0.0.1:8000/docs for the API. For frontend hot reload, leave FastAPI running and run `npm --prefix frontend run dev` in a second terminal. Open the Vite URL printed in that terminal. Vite proxies `/api` to FastAPI.

## Two-minute demo

1. Click **Load demo plate**. The synthetic plate should pass control quality checks with Z′ around 0.975.
2. Click **B06** in the plate map. Compare its signal and normalised inhibition, then filter **Well results** for `Compound-B`.
3. Upload `frontend/examples/demo-review.csv`. Its control means have the expected direction, but noisy controls cause a **Needs review** result. Compare it with the first run using the sidebar.
4. Export the annotated CSV and reload the page to verify that the saved runs remain. Upload the exact same file again to see that it opens the existing run without creating another.

## Data contract and calculations

The UTF-8 CSV header is `well,role,sample,signal`. Rows identify wells A1 to H12. Roles are `positive` (inhibited, low signal), `negative` (vehicle, high signal), and `sample`. Include at least three distinct wells for each control and one sample well. Each well can appear once. Missing wells are permitted. The upload limit is 256 KiB.

- **Z′ factor** = `1 - 3 × (SD_positive + SD_negative) / (mean_negative - mean_positive)`. The demo uses sample standard deviation and flags Z′ below 0.5 for review. It rejects reversed or effectively coincident control means because this workflow assumes inhibition lowers the signal.
- **Inhibition (%)** = `100 × (mean_negative - well_signal) / (mean_negative - mean_positive)`. Results can fall outside 0–100%. The map bounds its colour scale while the table and export retain those values. Results are rounded to two decimal places for display and export.
- The control threshold is a demonstration rule. Interpreting a real assay requires an assay-specific protocol and scientific review.

## Architecture

React and TypeScript handle file selection, run history, the plate map and filtering. FastAPI receives a bounded CSV stream, validates the schema, computes deterministic quality metrics and persists the complete analysed snapshot in SQLite. The database defaults to `assays.sqlite3` in the working directory. Set `ASSAY_DB` to override it.

An SHA-256 digest of the original bytes provides provenance and exact-byte deduplication. A database uniqueness constraint and atomic insert handle concurrent retries. If the same bytes are submitted again with a different run name, the original record wins. Changing line endings or row order produces a different digest. The database retains analysed well values and the digest. It does not retain the original file bytes.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Process health |
| POST | `/api/runs?name=Example` | Submit a raw `text/csv` body. Returns 201 for a new run or 200 for a duplicate |
| GET | `/api/runs` | List the 50 most recent runs |
| GET | `/api/runs/{id}` | Retrieve a run and all well measurements |
| GET | `/api/runs/{id}/export` | Download annotated CSV |

```sh
curl --fail-with-body -H 'Content-Type: text/csv' \
  --data-binary @frontend/examples/demo-pass.csv \
  'http://127.0.0.1:8000/api/runs?name=API%20demo'
python -m unittest backend.test_api -v
```

This is a local, single-user demo. It runs synchronously because one small plate is quick to analyse and SQLite keeps setup simple. There is no authentication or model deployment. An interview discussion could cover where to introduce object storage for source files, a queue for batch imports, or a downstream model consuming validated plate data. Present those as extensions to this personal project.
