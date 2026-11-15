# Handoff: rag-processor refactor

Last updated: 2026-11-12. Work is on branch `refactor/processor-only` (branched from `main` at `e970c11`). Commit `ed85709` covers Steps 0-3; the 2026-11-11 follow-up below (50 LLM columns + recursive ingestion) and the 2026-11-12 docs rewrite are committed on this branch.

## Goal
Reduce this repo to the **Processor** only: purchased mixed-format files (PDF, PPTX, DOCX, HTML, JSON, CSV/XLSX/XLS) are parsed, cleaned, chunked, embedded with bge-m3 (1024-dim) and ingested into LanceDB (cosine). Source (acquisition) and LLM are out of scope. Append-only batch load.

Original audit and plan: `refactoring_flags.md`. **Checklist for the remaining work: `NEXT_STEPS_PLAN.md`** (2026-11-12, supersedes the Step 4+ notes below). `README.md` and `Doc/current/` were rewritten on 2026-11-12 for the processor-only design.

## Progress

| Step | Status |
|---|---|
| 0. Verify claims | **Done** |
| 1. `schema.py` + `settings.py`; delete old SQLite/LanceDB artifacts | **Done** |
| 2. Delete Source-side files/deps; re-point `main.py`, `pipeline.py` | **Done** |
| 3. `type_router.py`, `parser.py`, `tabular.py`, `metadata.py` | **Done** (committed, awaiting user review) |
| 4. `cleaner.py`, `chunker.py` | **Next** |
| 5. `embedder.py`, `vector_store.py` | |
| 6. Multiprocess orchestration in `indexer.py`; wire tabular + metadata | |
| 7. Tests for the rest, CI, docs, repo cleanup | |

Rule of working: **one step at a time; stop after each and present a summary for review.**

## What exists now
- `config/settings.py`: all tunables, env-overridable, no import-time side effects (`ensure_dirs()` is explicit). Has a marked `LEGACY` block (path constants used by the old cleaner/chunker/indexer); delete it when those are rewritten.
- `pipeline/schema.py`: the contract. `ChunkRecord` (LanceDB row, `Vector(1024)`, plus the 50 nullable LLM columns listed in `EXTENDED_FIELDS`; `SCHEMA_VERSION` is 2), `SourceDocument`, `Chunk`, `Block`/`TableData`/`ParseResult` (parser IR), `build_record`, `make_chunk_id` (= sha256 of doc_id + chunk_index + text, 32 hex), `compute_doc_id` (sha256 of file bytes, 32 hex), `VECTOR_INDEX_KWARGS` (cosine).
- `pipeline/type_router.py`: `open_routed()` context manager; zips extracted to temp dirs that live until the block exits; limits and zip-slip protection; unsupported files returned with a reason.
- `pipeline/parser.py`: docling, item-class mapping, table structure kept, on-demand OCR (RapidOCR torch backend), docling on CPU / 1 thread per worker.
- `pipeline/tabular.py`: streams csv/xlsx/xls/json/jsonl to table `Chunk`s (header repeated, whole rows); `table_chunks()` for in-memory tables.
- `pipeline/metadata.py`: `build_source_document()`; sidecar `<file>.meta.json` and batch `metadata.json`/legacy `manifest.json`; never raises.
- `main.py ROOT` / `pipeline/pipeline.py`: ROOT is any directory (default `INPUT_DIR`) or a single file, crawled with `Path.rglob`. **No required layout; folder names and depth are ignored**; file types come only from `type_router` content sniffing. `discover_files()` skips hidden files/dirs, symlinks, sidecar/batch metadata files (`*.meta.json`, `metadata.json`, `manifest.json`) and the pipeline's own work dirs. Exit 1 if any file failed.
- `pipeline/metadata.py`: `MetadataResolver(root)` looks for batch files from ROOT down to each file's directory (nearest wins); `build_source_document` fills the 50 typed LLM columns from any key that exactly matches a column name (case-insensitive); unconvertible values become null and the original is kept in `metadata_json`.
- `pipeline/indexer.py`: `index_files(files, root)` replaces the per-unit `index_symbol`; `_index_file(item, doc)` is interim glue to the legacy cleaner/chunker/store.
- `tests/`: 50 tests. `pytest` runs 48; `RUN_SLOW=1 pytest` also runs the 2 model-backed PDF/OCR tests (first run downloads docling models).

## Decisions (user-confirmed)
- Data includes tabular numeric data; vendor metadata is inconsistent, so every field is optional and nothing may crash on missing metadata.
- LanceDB, bge-m3 1024-dim, **cosine**. Device order `cuda -> mps -> cpu`, no code change between machines.
- Multiprocessing start method **spawn**. CPU workers parse and chunk; the bge-m3 model lives in **one single MPS process** fed via batched queues (backpressure via `EMBED_QUEUE_MAX_BATCHES`).
- Tables embedded as text for now, but isolated (`content_kind="table"`) so they can move to a relational DB later.
- `allowed_roles` is a top-level nullable column (the OPA policy depends on it).
- `chunk_id` hashes doc_id + chunk_index + chunk text.
- Append-only: no superseded-filing handling.
- Table name is `documents` (was `company_documents`); the LLM side must read the new name.

## What to do next: Step 4 (cleaner + chunker)
From `refactoring_flags.md` plus the amendments:
1. **Cleaner** (`pipeline/cleaner.py`): consume `Block` objects, drop the dict-based `clean_ir_blocks` path, `_clean_numeric`, the file-based `run`/`_clean_document`.
   - Dynamic header/footer deletion currently removes any short block repeated on 3+ pages (deletes "Nil", "N.A.", recurring headings). Make it relative to page count and position-aware; never delete `header` blocks; log what was removed.
   - Stop encoding pages as `[[PAGE:n]]` markers in text. Carry `page_number` as structured data.
   - Do not collapse whitespace in tables, lists or code.
2. **Chunker** (`pipeline/chunker.py`): accept structured `Block`s, not one Markdown string.
   - Token-based sizing with the bge-m3 tokenizer (`CHUNK_SIZE_TOKENS` 512 / `CHUNK_OVERLAP_TOKENS` 64 are starting points). Add `tiktoken` or use the HF tokenizer (check `sentencepiece` not needed for the fast tokenizer).
   - `BLOCK_TABLE` blocks go through `tabular.table_chunks` (never split mid-row, header repeated); pass a token-based `size_fn`.
   - `page_range` computed from the blocks a chunk actually spans; `section_path` from header blocks.
   - IDs via `schema.make_chunk_id`. Delete `_global_chunk_id`, `run`, `_chunk_file`, the CHUNKED import, and the legacy dict path.
   - Make chunking a pure function (`chunk_document(blocks) -> list[Chunk]`) safe in a process pool (no shared counters, no torch import in workers).
3. Update `indexer.py` only as far as needed to keep the pipeline running (full rework is Step 6).
4. Tests: header/footer preservation on short docs, table header repeat, stable ids, `page_range` correctness, token limits respected.

Then Step 5: `embedder.py` (device order, `normalize_embeddings=True`, larger sorted batches, OOM backoff, logging not print, remove global chunk dedup and the legacy `Embedder` class) and `vector_store.py` (use `ChunkRecord`, `table.add` in batches, create cosine ANN index and scalar indexes once per run, no `merge_insert` by default, drop search helpers except a verification `get_by_doc_id`/`count`, parameterize filters).

## Known interim wrinkles (all fixed in later steps)
1. (Fixed 2026-11-11) folder names no longer leak into `symbol`; symbol comes only from vendor metadata.
2. Tabular files and `metadata.py` are not wired into the indexer; tabular files are counted as skipped.
3. Re-running already-ingested files counts them as failed (chunk-hash dedup returns 0 chunks), so exit code is 1 on re-runs.
4. `normalizer.py` and `classifier.py`/`rules.yaml` are orphaned (no stage calls them); delete or keep classifier as a fallback in Step 7.
5. Old cleaner/chunker still work on the dict form of blocks via `Block.to_dict()`.
6. `.gitignore` still lacks `data/lancedb/`, `data/logs/`, `data/input/`, `.DS_Store`; README and `Doc/current/*` describe removed stages (Step 7).

## Risks to keep in mind
- Never fork after torch/MPS is initialised. Load the embedding model only in the embedder process.
- Numeric accuracy: embeddings are poor at exact numbers; keep tables isolated for a future structured store.
- RapidOCR covers English and Chinese only (no Hindi). Page header/footer separation on messy real filings is untested; try a few purchased documents.
- `beautifulsoup4`, `httpx`, `tenacity` remain in `uv.lock` as transitive deps of docling / huggingface-hub / langchain-core; they cannot be removed.

## How to resume
1. `cd /Users/arnav/Projects/Software/rag-processor && git checkout refactor/processor-only && git status`
2. `uv sync` if `.venv` is missing; `.venv/bin/python -m pytest -q` should show 48 passed, 2 skipped (50 passed with `RUN_SLOW=1`, which downloads models on first run).
3. Tell Claude: "Read HANDOFF.md and NEXT_STEPS_PLAN.md, then continue with Step 4."

## 2026-11-11 follow-up
- Added the 50 downstream-LLM columns to `ChunkRecord` as nullable typed columns (floats, ints, bools, strings; dates/timestamps are ISO strings as supplied). `Chunk.fields` overrides `SourceDocument.fields`, so a record-level chunk can carry its own price/fundamental values.
- **Open design question for Step 6:** price/fundamental CSVs are currently chunked as row groups (many rows per chunk), where per-row columns like `close` have no single value. Decide whether tabular files whose columns match these fields should be emitted one chunk per record (with the columns filled) instead.
- Chosen types (not specified by the user, change if the LLM side differs): `total_traded_qty`, `deliverable_qty`, `no_of_trades`, `futures_oi` are int64; `is_fno`, `bulk_block_deal_flag` are bool; everything else numeric is float64; `trade_date`, `quarter_ending`, `broadcast_timestamp` are strings.
- Duplicate files at different paths are not deduplicated yet (document-level dedup by `doc_id` arrives in Step 6).
