# Handoff: rag-processor refactor

Last updated: 2026-11-16. Branch `refactor/processor-only` (branched from `main` at `e970c11`), pushed to `origin`.

## Goal
Reduce this repo to the **Processor** only: purchased mixed-format files (PDF, PPTX, DOCX, HTML, JSON, CSV/XLSX/XLS) are parsed, cleaned, chunked, embedded with bge-m3 (1024-dim) and ingested into LanceDB (cosine). Source (acquisition) and LLM are out of scope. Append-only batch load.

## Progress

| Step | Status |
|---|---|
| 0. Verify claims | Done |
| 1. `schema.py` + `settings.py` | Done |
| 2. Delete Source-side files/deps; recursive-root `main.py`, `pipeline.py` | Done |
| 3. `type_router.py`, `parser.py`, `tabular.py`, `metadata.py` | Done |
| 4. `cleaner.py`, `chunker.py` | Done |
| 5. `embedder.py`, `vector_store.py` | Done |
| 6. Multiprocess `indexer.py` + `worker.py`; tabular and metadata wired in | Done |
| 7. Tests, CI workflow, docs, repo cleanup | Done |

**What remains** is validation on real purchased data and a few decisions for the LLM side. See `NEXT_STEPS_PLAN.md`, section 4.

## Where to read
- `README.md`: what it does, how to run it.
- `Doc/current/01-06`: overview, ingestion workflow, schema, processing stages, configuration, development.
- `NEXT_STEPS_PLAN.md`: what was built per step, decisions taken, what is left.
- `refactoring_flags.md`: the original audit (historical).

## Decisions (user-confirmed)
- Data includes tabular numeric data; vendor metadata is inconsistent, so every field is optional and nothing may crash on missing metadata.
- No required input layout: one ROOT, crawled with `Path.rglob`; folder names carry no meaning; file types come from content sniffing.
- LanceDB, bge-m3 1024-dim, **cosine**. Device order `cuda -> mps -> cpu`, no code change between machines.
- Multiprocessing start method **spawn**. CPU workers parse and chunk; bge-m3 lives in **one** process fed via bounded queues.
- Tables embedded as text for now, but isolated (`content_kind="table"`, `table_id`) so they can move to a relational DB later.
- `allowed_roles` is a top-level nullable column (the OPA policy depends on it).
- `chunk_id` hashes doc_id + chunk_index + chunk text.
- 50 downstream-LLM columns are nullable typed columns on `ChunkRecord`.
- Append-only: no superseded-filing handling.
- Table name is `documents` (was `company_documents`); the LLM side must read the new name.

## Decisions taken by Claude without explicit confirmation (review these)
- Record mode is on: price/fundamentals-style sheets become one chunk per row with typed columns filled (`TABULAR_RECORD_MODE=off` disables).
- The regex `doc_type` classifier and `normalizer.py` were deleted.
- Null `allowed_roles` is stored as null; no default.
- `.txt`/`.md` are not supported.
- Types chosen for the 50 columns: four ints, two bools, dates as strings, the rest floats.

## Working rules
- Commit after every major change, in small logical commits, for easy rollback.
- **No `Co-Authored-By` or other Claude attribution** in commits or PRs. Push only as the user over SSH.

## Risks to keep in mind
- Never fork after torch/MPS is initialised. Load the embedding model only in the embedder process.
- Numeric accuracy: embeddings are poor at exact numbers; keep tables isolated for a future structured store.
- RapidOCR covers English and Chinese only (no Hindi).
- Record mode on very large price files produces one vector per row.
- Each PDF worker holds docling's models in memory; tune `NUM_WORKERS` on the M4 Pro.

## How to resume
1. `cd /Users/arnav/Projects/Software/rag-processor && git checkout refactor/processor-only && git status`
2. `uv sync` if `.venv` is missing; `.venv/bin/python -m pytest -q` should pass (a few tests are skipped without `RUN_SLOW=1`).
3. Try a real delivery: `uv run python main.py <folder> --dry-run`, then without `--dry-run`.
4. Tell Claude: "Read HANDOFF.md and NEXT_STEPS_PLAN.md, then help me validate on real data."
