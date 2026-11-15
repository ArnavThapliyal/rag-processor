# 6. Development

## Environment

```bash
brew install libmagic
uv sync                 # Python 3.11, dependencies from uv.lock
```

Runtime dependencies: `docling`, `lancedb`, `sentence-transformers`, `torch`, `pandas`, `pyarrow`, `openpyxl`, `xlrd`, `python-magic`, `pyyaml`, `tabulate`, `langchain-text-splitters` (used only by the legacy chunker). Dev: `pytest`, `ruff`, `reportlab` (builds PDF fixtures).

No scraping libraries are declared. `beautifulsoup4`, `httpx` and `tenacity` still appear in `uv.lock` because docling, huggingface-hub and langchain-core depend on them.

## Repository layout

```
main.py                     CLI entry point
config/settings.py          configuration
pipeline/
  pipeline.py               discovery + PipelineRunner
  type_router.py            type detection, zip handling
  metadata.py               vendor metadata -> SourceDocument
  parser.py                 docling -> Blocks
  tabular.py                csv/xlsx/xls/json -> table Chunks
  schema.py                 ChunkRecord, ids, build_record, parser IR
  indexer.py                per-file orchestration
  cleaner.py chunker.py embedder.py vector_store.py     legacy, pending rewrite
  normalizer.py document_template/                      orphaned
tests/
  test_*.py                 Processor tests
  *.rego                    OPA policies owned by the LLM stage
Doc/current/                this documentation
NEXT_STEPS_PLAN.md          remaining work
HANDOFF.md                  progress notes between sessions
refactoring_flags.md        original audit and plan
```

## Tests

```bash
uv run pytest               # 48 tests, a few seconds, no model downloads
RUN_SLOW=1 uv run pytest    # plus 2 tests that run docling on real PDFs (text + scanned)
```

| File | Covers |
|---|---|
| `test_schema.py` | ids, 1024-dim fixed vector, cosine, LanceDB round-trip with and without metadata, the 50 financial columns, coercion |
| `test_type_router.py` | content sniffing, mislabelled files, zip lifetime and safety |
| `test_metadata.py` | missing/malformed metadata, aliases, precedence, nearest-directory resolution |
| `test_tabular.py` | header repetition, whole rows, encodings, truncation |
| `test_parser.py` | DOCX and HTML block mapping (no models needed) |
| `test_parser_pdf.py` | PDF tables and OCR (slow, gated by `RUN_SLOW=1`) |
| `test_pipeline.py` | discovery on an irregular tree, run summary, routing by content, failure isolation |

There are no tests yet for the cleaner, chunker, embedder, store, or a full run into LanceDB.

CI: `.github/workflows/opatest.yml` runs the OPA policy tests. `.github/workflows/pipeline-test.yml` is empty; a `ruff` + `pytest` workflow is planned.

## Conventions

- Configuration comes from `config/settings.py` only. No hard-coded paths, model names or sizes in modules.
- Vendor metadata handling must not raise. Return null and log a warning.
- New rows are built only through `schema.build_record`.
- Do not import torch or load models at module import time. Worker processes are started with `spawn`, and the embedding model must exist in one process only.
- Use `logging`, not `print`, inside `pipeline/`.
- Keep table content identifiable: table chunks use `content_kind="table"` and carry their sheet and row range.
- Bump `PIPELINE_VERSION` or `SCHEMA_VERSION` when behaviour or the schema changes.

## Shell note

The default shell on macOS is zsh, which does not split unquoted variables into words. Run multi-path shell one-liners under `bash -c '...'`.
