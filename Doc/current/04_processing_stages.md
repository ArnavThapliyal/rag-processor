# 4. Processing stages

Each stage is described as it behaves **today**, followed by the target where the two differ. The checklist for closing each gap is in `NEXT_STEPS_PLAN.md`.

## Parser (`pipeline/parser.py`) — current

`Parser().parse(path)` returns a `ParseResult`: a list of `Block`s, the page count, the pages that were OCRed, and warnings.

- Uses docling for PDF, DOCX, PPTX and HTML.
- Block types: `header` (with level), `paragraph`, `list_item`, `table`, `caption`, `footnote`, `code`. Each block carries a `page_number`, or `None` for formats without pages.
- Tables keep their structure as `TableData` (header + rows) in addition to Markdown text. A first row that looks like a header is promoted to the header.
- Page headers and footers are in docling's furniture layer and are excluded.
- **OCR is on demand.** A document is first parsed without OCR. If pages have fewer than `min_chars_per_page_before_ocr` characters and their count is within `max_pages_for_ocr`, the document is converted again with OCR (RapidOCR, torch backend). RapidOCR covers English and Chinese, not Hindi.
- Docling runs on CPU with one thread (`PARSER_DEVICE`, `PARSER_THREADS`), so several parser processes can run side by side and the embedding process is the only user of the GPU/MPS device.

## Tabular (`pipeline/tabular.py`) — current, not yet wired in

`read_table_chunks(path, source_type, ...)` streams a CSV, XLSX, XLS or JSON/JSONL file and yields `Chunk`s with `content_kind="table"`.

- Rows are grouped (`TABULAR_ROWS_PER_CHUNK`, also bounded by a size limit) and rendered as a Markdown table. A row is never split and **every chunk repeats the header row**.
- Chunk metadata records `sheet`, `row_start`, `row_end` and `columns`, which is what a later relational load needs to find the same rows again.
- Blank or duplicate header cells are repaired. CSV encoding falls back from UTF-8 to cp1252 to latin-1, and the delimiter is sniffed.
- Nested JSON objects are flattened to dotted column names.
- Files longer than `TABULAR_MAX_ROWS_PER_FILE` are truncated with a warning.
- `table_chunks(table, ...)` applies the same grouping to a table found inside a document.

The indexer does not call this module yet, so tabular files are currently skipped.

## Cleaner (`pipeline/cleaner.py`) — legacy

Today:

- Works on dict versions of blocks (`Block.to_dict()`), then flattens everything to one Markdown string.
- Deletes any short block (under 80 characters) that appears on three or more pages. On real filings this removes legitimate repeated content such as "Nil", "N.A." and recurring section headings.
- Encodes page numbers into the text as `[[PAGE:n]]` markers, emitted only when the page changes, so chunks that lie within a page lose their page.
- Renders table blocks as ordinary paragraphs.
- Still contains file-based code paths for directories that no longer exist.

Target: operate on `Block` objects, remove repeated boilerplate only when it is frequent relative to the page count and never remove headings or tables, keep page numbers as data, and log what was removed.

## Chunker (`pipeline/chunker.py`) — legacy

Today:

- Splits the cleaner's Markdown string by headings, then by characters (1000 characters, 100 overlap, hard-coded).
- Produces ids of the form `md5(text)[:6]_index`, which are not document-scoped.
- Recovers `page_range` from the `[[PAGE:n]]` markers, falling back to `"unknown"`.
- Can split a table in the middle of a row.

Target: a pure function over `Block`s that sizes chunks in bge-m3 tokens (`CHUNK_SIZE_TOKENS`, `CHUNK_OVERLAP_TOKENS`), sends table blocks through `tabular.table_chunks`, computes `page_range` and `section_path` from the blocks a chunk spans, and returns `schema.Chunk` objects. Ids come from `schema.make_chunk_id`.

## Embedder (`pipeline/embedder.py`) — legacy

Today:

- Loads `BAAI/bge-m3` through sentence-transformers and checks the output is 1024-dim.
- Device order is `mps → cuda → cpu` (the required order is `cuda → mps → cpu`).
- Encodes in batches of 32 without normalising the vectors.
- Drops any chunk whose text hash already exists anywhere in the table, across all documents. Identical text in two documents is therefore stored once, and a re-run returns no chunks, which the indexer reports as a failure.
- Uses its own hard-coded database path and table name.

Target: device order `cuda → mps → cpu` with an `EMBED_DEVICE` override, `normalize_embeddings=True`, length-sorted batches of `EMBED_BATCH_SIZE`, out-of-memory backoff, no chunk-level deduplication, and the model loaded in exactly one process.

## Vector store (`pipeline/vector_store.py`) — legacy

Today:

- Writes table `company_documents` with its own schema: `content` instead of `text`, a variable-length `List[float]` vector (which cannot be ANN-indexed), no `doc_id`, and none of the financial columns.
- Upserts with `merge_insert("chunk_id")`, once per file, opening a new connection each time.
- Uses a path relative to the current working directory.
- Builds filter strings by interpolating values.
- Contains search helpers that belong to the LLM stage.

Target: create or open `documents` from `ChunkRecord`, append with `table.add` in batches, build the cosine vector index and scalar indexes once per run, and expose only `count` and `get_by_doc_id` for verification.

## Indexer (`pipeline/indexer.py`) — half-migrated

Today `Indexer.index_files(files, root)`:

- opens each file with `open_routed`, builds the `SourceDocument`, and isolates failures per file (this part is final);
- runs document files through parser → legacy cleaner → legacy chunker → legacy embedder → legacy store, in one process;
- counts tabular and unsupported files as skipped;
- treats "zero chunks written" as a failure.

Target: the orchestrator of a multi-process run.

```mermaid
flowchart LR
    main["main process<br/>discover, dedup by doc_id,<br/>write to LanceDB"] -->|file paths| pool["N CPU workers (spawn)<br/>route, metadata, parse,<br/>clean, chunk"]
    pool -->|"chunk batches<br/>(bounded queue)"| emb["1 embedding process<br/>bge-m3 on cuda/mps/cpu"]
    emb -->|vectors| main
```

- Start method `spawn`; torch and the embedding model are never initialised before the workers start.
- The queue between workers and the embedder is bounded (`EMBED_QUEUE_MAX_BATCHES`), so fast parsers cannot fill memory.
- A document already present in the table (same `doc_id`) is skipped and reported as skipped, not failed.

## Orphaned modules

`pipeline/normalizer.py` and `pipeline/document_template/` (a four-rule regex classifier for document types) are not called by anything. They will be deleted, unless the classifier is kept as a fallback for a missing `doc_type`.
