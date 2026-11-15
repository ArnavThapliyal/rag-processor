# 5. Configuration

All configuration is in `config/settings.py`. Each setting below can be overridden by an environment variable **of the same name**, unless marked fixed. Importing the module creates no directories; `main.py` calls `ensure_dirs()` after argument parsing.

> One exception today: a block marked `LEGACY` at the bottom of the file still creates `data/raw`, `data/transient`, `data/cleaned`, `data/chunked` and `data/done` at import time for the old cleaner and chunker. It disappears with those modules.

## Paths

| Setting | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `<repo>/data` | Parent of the defaults below |
| `INPUT_DIR` | `DATA_DIR/input` | Default ROOT when `main.py` gets no argument |
| `SCRATCH_DIR` | `DATA_DIR/scratch` | Temporary zip extraction |
| `LOG_DIR` | `DATA_DIR/logs` | `pipeline.log` |
| `LANCEDB_PATH` | `DATA_DIR/lancedb` | Database directory |

## Vector store

| Setting | Default | Notes |
|---|---|---|
| `LANCEDB_TABLE` | `documents` | The LLM stage must read this name |
| `DISTANCE_METRIC` | `cosine` | Fixed; part of the contract |

## Embedding

| Setting | Default | Notes |
|---|---|---|
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Changing the model requires a new table |
| `EMBEDDING_DIM` | `1024` | Fixed |
| `EMBED_DEVICE` | unset | Unset means auto: `cuda → mps → cpu`. Set `cuda`, `mps` or `cpu` to force |
| `EMBED_BATCH_SIZE` | `64` | Texts per encode call |
| `EMBED_FP16` | `false` | Off until bge-m3 quality in half precision is checked |
| `EMBED_QUEUE_MAX_BATCHES` | `8` | Capacity of the worker → embedder queue (backpressure) |

## Chunking

| Setting | Default | Notes |
|---|---|---|
| `CHUNK_SIZE_TOKENS` | `512` | In bge-m3 tokens. Starting point, to be tuned with a retrieval evaluation |
| `CHUNK_OVERLAP_TOKENS` | `64` | |
| `TABULAR_ROWS_PER_CHUNK` | `40` | Maximum rows per table chunk |
| `TABULAR_MAX_ROWS_PER_FILE` | `2000000` | Rows beyond this are dropped with a warning |

## Parallelism

| Setting | Default | Notes |
|---|---|---|
| `MP_START_METHOD` | `spawn` | Fixed. Forking after torch/MPS initialises is unsafe on macOS |
| `NUM_WORKERS` | CPU count − 2 | Parse/chunk workers. Two cores are left for the main and embedding processes |

## Parsing

| Environment variable | Default | Notes |
|---|---|---|
| `ENABLE_ML_OCR` | `true` | Allow the OCR second pass |
| `MAX_PAGES_FOR_OCR` | `50` | OCR is skipped when more low-text pages than this are found |
| `MIN_CHARS_PER_PAGE_BEFORE_OCR` | `25` | A page with fewer characters counts as low-text |
| `TABLE_STRUCTURE_MODE` | `accurate` | `fast` or `accurate` (docling TableFormer) |
| `PARSER_DEVICE` | `cpu` | `cpu`, `auto`, `mps`, `cuda`. Keep `cpu` when running several workers |
| `PARSER_THREADS` | `1` | Threads per parser |

These four OCR/table values are exposed in code as the `COMPUTE_BUDGET` dict.

## Zip limits

| Setting | Default |
|---|---|
| `ZIP_MAX_MEMBERS` | `5000` |
| `ZIP_MAX_UNCOMPRESSED_BYTES` | 4 GiB |
| `ZIP_MAX_DEPTH` | `3` |

## Versioning

| Setting | Value | Bump when |
|---|---|---|
| `PIPELINE_VERSION` | `0.1.0` | Parse, clean or chunk behaviour changes |
| `SCHEMA_VERSION` | `2` | `ChunkRecord` changes |

## Running on different hardware

The code is the same on every machine; only environment variables differ.

| Machine | Suggested overrides |
|---|---|
| Apple Silicon (M4 Pro) | None. Device resolves to `mps` |
| CUDA server | None for the device. Consider a larger `EMBED_BATCH_SIZE`, and `EMBED_FP16=true` after a quality check |
| CPU only | Lower `EMBED_BATCH_SIZE`; expect slow embedding |

Until `embedder.py` is rewritten, `EMBED_DEVICE`, `EMBED_BATCH_SIZE`, `LANCEDB_PATH` and `LANCEDB_TABLE` are not honoured by the legacy embedder and store.

## System dependency

`python-magic` needs the libmagic library: `brew install libmagic` on macOS, `apt-get install libmagic1` on Debian/Ubuntu.
