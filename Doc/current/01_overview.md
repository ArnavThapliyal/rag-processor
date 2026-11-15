# 1. Overview

## Scope

The RAG system is split into three stages owned separately:

| Stage | Responsibility | In this repo |
|---|---|---|
| Source | Acquire data. The data is now purchased and arrives as static files. | No |
| **Processor** | Parse, clean, chunk, embed, and load files into LanceDB. | **Yes** |
| LLM | Retrieval, authorisation, prompting, answering. | No (only its OPA policies are mirrored under `tests/`) |

The Processor is an **append-only batch loader**. It does not watch folders, does not run continuously, and does not handle superseded filings.

## Data flow

1. **Discover.** `main.py ROOT` crawls ROOT recursively with `Path.rglob`. No folder layout is required.
2. **Route.** `type_router` detects each file's type from its content and picks a handler. Zip archives are unpacked and their members routed the same way.
3. **Describe.** `metadata` builds a `SourceDocument` from whatever vendor metadata exists. Every field is optional.
4. **Extract.**
   - Documents (PDF, DOCX, PPTX, HTML) go through `parser` (docling), which returns structured blocks: headers, paragraphs, list items, tables.
   - Tabular files (CSV, XLSX, XLS, JSON) go through `tabular`, which streams rows straight into table chunks.
5. **Clean and chunk** (documents only). Boilerplate is removed and blocks are grouped into chunks. Tables are never split inside a row and repeat their header.
6. **Embed.** `BAAI/bge-m3` produces one 1024-dim vector per chunk.
7. **Load.** `schema.build_record` validates each row, and the store appends it to the `documents` table.

## Module map

| Module | Role | State |
|---|---|---|
| `main.py` | CLI, logging, exit code | Current |
| `config/settings.py` | All tunables, environment overrides | Current (contains a legacy block to delete) |
| `pipeline/pipeline.py` | `discover_files`, `PipelineRunner` | Current |
| `pipeline/type_router.py` | Type detection, zip extraction | Current |
| `pipeline/metadata.py` | Vendor metadata → `SourceDocument` | Current |
| `pipeline/parser.py` | docling → `Block` list, on-demand OCR | Current |
| `pipeline/tabular.py` | Tabular files → table `Chunk`s | Current, not yet called by the indexer |
| `pipeline/schema.py` | `ChunkRecord`, ids, `build_record`, parser IR | Current, not yet used by the store |
| `pipeline/indexer.py` | Per-file orchestration | Half-migrated: new routing, legacy processing glue |
| `pipeline/cleaner.py` | Boilerplate removal | Legacy |
| `pipeline/chunker.py` | Chunking | Legacy |
| `pipeline/embedder.py` | Embedding | Legacy |
| `pipeline/vector_store.py` | LanceDB writes | Legacy |
| `pipeline/normalizer.py`, `pipeline/document_template/` | Old knowledge-file writer and doc-type classifier | Orphaned, nothing calls them |

"Legacy" means the module predates the refactor and still uses dict-based chunks, character-based sizes, the old table, and the old ids. See [04_processing_stages.md](04_processing_stages.md) for what each one does today and [../../NEXT_STEPS_PLAN.md](../../NEXT_STEPS_PLAN.md) for the rewrite checklist.

## Design decisions

- **No required input layout.** Folder names are never interpreted. The tree matters only for finding batch metadata files.
- **Nothing crashes on missing metadata.** Vendor metadata is inconsistent, so every vendor field is nullable and unknown fields are preserved in `metadata_json`.
- **Deterministic ids.** `doc_id` is derived from file bytes and `chunk_id` from `doc_id`, chunk index and chunk text, so the same input always yields the same ids.
- **Tables are isolated.** Table chunks carry `content_kind="table"` so they can later move to a relational store without re-parsing. For now they are embedded as text.
- **One process owns the embedding device.** Target design: CPU worker processes parse and chunk; a single process holds bge-m3 on the device (`cuda → mps → cpu`) and receives batches through a bounded queue. Start method is `spawn`.
- **`allowed_roles` is a real column.** The OPA policies in `tests/rag_authz.rego` read it, so it is not buried in `metadata_json`.
