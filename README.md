# rag-processor

The **Processor** stage of a three-part RAG system (Source → Processor → LLM).

It takes a folder of purchased files in mixed formats, parses, cleans, chunks and embeds them with `BAAI/bge-m3`, and appends the result to a LanceDB table that the LLM stage reads. Acquiring the data (Source) and answering questions (LLM) live elsewhere.

```mermaid
flowchart LR
    root["ROOT folder<br/>(any layout)"] --> discover["discover<br/>Path.rglob"]
    discover --> route["type_router<br/>content sniffing"]
    route -->|pdf, docx, pptx, html| parse["parser (docling)"]
    route -->|csv, xlsx, xls, json| tab["tabular"]
    meta["metadata<br/>sidecar / batch files"] --> record
    parse --> clean["cleaner"] --> chunk["chunker"]
    chunk --> embed["embedder<br/>bge-m3, 1024-dim"]
    tab --> embed
    embed --> record["build_record"] --> db[("LanceDB<br/>cosine")]
```

## Status

The refactor from the old scraping pipeline is in progress. Read this before relying on a run.

| Part | State |
|---|---|
| Recursive discovery, CLI, per-file failure isolation | Working, tested |
| Type detection, zip handling | Working, tested |
| Document parsing (PDF, DOCX, PPTX, HTML), on-demand OCR | Working, tested |
| Tabular reading (CSV, XLSX, XLS, JSON) into table chunks | Working, tested, **not yet wired into a run** |
| Vendor metadata resolution | Working, tested |
| LanceDB schema (1024-dim vector, cosine, 50 financial columns), ids | Defined and tested, **not yet what a run writes** |
| Cleaner, chunker, embedder, vector store | **Legacy code**, pending rewrite |
| Multiprocessing (CPU workers + one embedding process) | **Not implemented**; runs are single-process |

Until the remaining steps land, `main.py` still pushes documents through the legacy cleaner, chunker, embedder and store. That path writes the **old** table (`company_documents`, old columns, vectors not normalised), skips tabular files, and reports a re-run of already-ingested files as failures. The remaining work is itemised in [NEXT_STEPS_PLAN.md](NEXT_STEPS_PLAN.md).

## Setup

```bash
brew install libmagic        # required by python-magic (content sniffing)
uv sync                      # creates .venv from uv.lock (Python 3.11)
```

The first run that parses a PDF downloads docling's layout and table models; the first embedding run downloads `BAAI/bge-m3`.

## Usage

```bash
uv run python main.py /path/to/purchased/data   # a directory, crawled recursively
uv run python main.py /path/to/one/file.pdf     # or a single file
uv run python main.py                           # defaults to data/input
```

- There is **no required folder structure**. Folder names and depth carry no meaning.
- File types are detected from file content, with the extension as a hint. A PDF in a folder named `csv` is still a PDF.
- One bad file never stops the run. The exit code is `1` if any file failed, otherwise `0`.
- Logs go to stdout and `data/logs/pipeline.log`.

### What is skipped during discovery

- Hidden files and anything inside a hidden directory (`.git`, `.DS_Store`).
- Symlinks.
- The pipeline's own working directories (scratch, logs, LanceDB).
- Metadata files: `*.meta.json`, `metadata.json`, `manifest.json`. They are read, never ingested. A real data file with one of these names will be skipped, so rename it.

Unsupported files (`.doc`, `.ppt`, unknown types) are counted as skipped and logged with a reason.

### Vendor metadata (all optional)

Nothing fails when metadata is missing or malformed; the field is simply null.

| Source | Location | Scope |
|---|---|---|
| Sidecar | `<filename>.meta.json` next to the file | That file. Highest priority. |
| Batch file | `metadata.json` in the file's directory or any parent up to ROOT | `{"defaults": {...}, "files": {"<filename>": {...}}}`. The nearest directory wins. |

Common vendor spellings are mapped (`ticker` → `symbol`, `url` → `source_url`, `roles` → `allowed_roles`, and so on). A key that exactly matches one of the financial columns (for example `isin`, `close`, `roe_pct`) fills that column. Anything unrecognised is kept in `metadata_json`.

## The LanceDB contract

| Item | Value |
|---|---|
| Table | `documents` |
| Vector column | `embedding`, fixed-size list of 1024 float32 (bge-m3 dense) |
| Distance | cosine |
| `doc_id` | first 32 hex chars of `sha256(file bytes)` |
| `chunk_id` | first 32 hex chars of `sha256(doc_id \0 chunk_index \0 chunk text)` |
| `content_kind` | `prose` or `table` (tables stay separable for a later relational store) |
| `allowed_roles` | top-level nullable list of strings (read by the OPA policies) |
| Financial columns | 50 nullable typed columns (prices, fundamentals, shareholding, announcements) |

LanceDB does not store the metric in the table schema. It is applied when an index is built and when a query runs, so readers must search with `metric("cosine")` (see `VECTOR_INDEX_KWARGS` in `pipeline/schema.py`).

Full column list and coercion rules: [Doc/current/03_schema.md](Doc/current/03_schema.md).

## Configuration

Everything is in `config/settings.py` and can be overridden with an environment variable of the same name, for example:

```bash
NUM_WORKERS=8 EMBED_BATCH_SIZE=128 LANCEDB_PATH=/mnt/db uv run python main.py /data/in
```

The embedding device is chosen automatically in the order `cuda → mps → cpu`; set `EMBED_DEVICE` to force one. See [Doc/current/05_configuration.md](Doc/current/05_configuration.md).

## Tests

```bash
uv run pytest               # 48 fast tests, no model downloads
RUN_SLOW=1 uv run pytest    # also the 2 docling PDF/OCR tests (downloads models once)
```

The `.rego` files under `tests/` are OPA policies for the LLM side and are run by `.github/workflows/opatest.yml`. They are kept here because the schema's `allowed_roles` column must stay compatible with them.

## Documentation

| File | Contents |
|---|---|
| [Doc/current/01_overview.md](Doc/current/01_overview.md) | Scope, data flow, module map |
| [Doc/current/02_ingestion_workflow.md](Doc/current/02_ingestion_workflow.md) | Discovery, routing, metadata, failure model |
| [Doc/current/03_schema.md](Doc/current/03_schema.md) | LanceDB columns, ids, vector and metric, financial fields |
| [Doc/current/04_processing_stages.md](Doc/current/04_processing_stages.md) | Parser, tabular, cleaner, chunker, embedder, store |
| [Doc/current/05_configuration.md](Doc/current/05_configuration.md) | Settings and environment variables |
| [Doc/current/06_development.md](Doc/current/06_development.md) | Repo layout, tests, conventions |
| [NEXT_STEPS_PLAN.md](NEXT_STEPS_PLAN.md) | File-by-file checklist for the remaining work |
| [HANDOFF.md](HANDOFF.md) | Session-to-session progress notes |
| [refactoring_flags.md](refactoring_flags.md) | The original audit and approved plan |
