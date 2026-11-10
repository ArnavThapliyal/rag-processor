"""
Single source of truth for Processor configuration.

Every tunable can be overridden by an environment variable of the same name so
the identical codebase runs on a local M-series Mac and on a CUDA cloud box
without edits. Importing this module has no filesystem side effects; call
ensure_dirs() explicitly (main.py does) to create the working directories.
"""

import os
from pathlib import Path
from typing import Callable, Dict, Optional, TypeVar

T = TypeVar("T")


def _env(name: str, default: T, cast: Callable[[str], T] = str) -> T:  # type: ignore[assignment]
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return cast(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---- paths ------------------------------------------------------------------

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = Path(_env("DATA_DIR", str(BASE_DIR / "data")))

INPUT_DIR = Path(_env("INPUT_DIR", str(DATA_DIR / "input")))      # purchased files land here
SCRATCH_DIR = Path(_env("SCRATCH_DIR", str(DATA_DIR / "scratch")))  # zip extraction, per-document temp
LOG_DIR = Path(_env("LOG_DIR", str(DATA_DIR / "logs")))

# ---- vector store (contract with the LLM side) ----------------------------------

LANCEDB_PATH = Path(_env("LANCEDB_PATH", str(DATA_DIR / "lancedb")))
LANCEDB_TABLE = _env("LANCEDB_TABLE", "documents")
DISTANCE_METRIC = "cosine"  # fixed by the contract; not env-overridable on purpose

# ---- embedding --------------------------------------------------------------

EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_DIM = 1024  # bge-m3 dense output; changing the model means a new table
EMBED_DEVICE: Optional[str] = _env("EMBED_DEVICE", None) or None  # None -> auto: cuda -> mps -> cpu
EMBED_BATCH_SIZE = _env("EMBED_BATCH_SIZE", 64, int)
EMBED_FP16 = _env_bool("EMBED_FP16", False)  # off until bge-m3 quality in fp16 is checked
EMBED_QUEUE_MAX_BATCHES = _env("EMBED_QUEUE_MAX_BATCHES", 8, int)  # backpressure between workers and embedder

# ---- chunking ---------------------------------------------------------------

# Measured in tokens of EMBEDDING_MODEL's tokenizer. bge-m3 accepts 8192; these
# are conservative starting points to be tuned against a retrieval eval.
CHUNK_SIZE_TOKENS = _env("CHUNK_SIZE_TOKENS", 512, int)
CHUNK_OVERLAP_TOKENS = _env("CHUNK_OVERLAP_TOKENS", 64, int)

# Tabular sources (csv/xlsx/json). Chunks are whole row groups with the header repeated.
TABULAR_ROWS_PER_CHUNK = _env("TABULAR_ROWS_PER_CHUNK", 40, int)
TABULAR_MAX_ROWS_PER_FILE = _env("TABULAR_MAX_ROWS_PER_FILE", 2_000_000, int)

# ---- parallelism ----------------------------------------------------------------

# 'spawn' is mandatory: forking after torch/MPS initialises is unsafe on macOS.
MP_START_METHOD = "spawn"
# Parsing/chunking workers. Reserve two cores: one for the main process and
# one for the single embedding process, which owns the device.
NUM_WORKERS = _env("NUM_WORKERS", max(1, (os.cpu_count() or 4) - 2), int)

# ---- parsing ----------------------------------------------------------------

# OCR uses docling's RapidOCR default model (English + Chinese); Hindi is not covered.
COMPUTE_BUDGET: Dict[str, object] = {
    "enable_ml_ocr": _env_bool("ENABLE_ML_OCR", True),
    "max_pages_for_ocr": _env("MAX_PAGES_FOR_OCR", 50, int),
    "min_chars_per_page_before_ocr": _env("MIN_CHARS_PER_PAGE_BEFORE_OCR", 25, int),
    "table_structure_mode": _env("TABLE_STRUCTURE_MODE", "accurate", str),  # fast | accurate
}

# Docling's layout/table models run inside each parse worker. Keep them on CPU
# with one thread each so N workers don't oversubscribe cores, and so the single
# embedding process is the only user of the MPS/CUDA device.
PARSER_DEVICE = _env("PARSER_DEVICE", "cpu", str)  # cpu | auto | mps | cuda
PARSER_THREADS = _env("PARSER_THREADS", 1, int)

# Zip handling limits (type_router).
ZIP_MAX_MEMBERS = _env("ZIP_MAX_MEMBERS", 5_000, int)
ZIP_MAX_UNCOMPRESSED_BYTES = _env("ZIP_MAX_UNCOMPRESSED_BYTES", 4 * 1024**3, int)
ZIP_MAX_DEPTH = _env("ZIP_MAX_DEPTH", 3, int)

# ---- provenance ---------------------------------------------------------------

PIPELINE_VERSION = "0.1.0"  # bump when parse/clean/chunk behaviour changes
SCHEMA_VERSION = 1          # bump when the LanceDB schema in pipeline/schema.py changes


def ensure_dirs() -> None:
    """Create working directories. Called explicitly; never at import time."""
    for path in (DATA_DIR, INPUT_DIR, SCRATCH_DIR, LOG_DIR, LANCEDB_PATH):
        path.mkdir(parents=True, exist_ok=True)


# ============================================================================
# LEGACY — consumed by pre-refactor modules (cleaner, chunker, indexer).
# Removed together with those code paths in Steps 4-6. Do not use in new code.
# ============================================================================

RAW_NUMERIC = BASE_DIR / "data" / "raw" / "numeric"
RAW_DOCUMENTS = BASE_DIR / "data" / "raw" / "documents"
TRANS_NUMERIC = BASE_DIR / "data" / "transient" / "numeric"
TRANS_DOCUMENTS = BASE_DIR / "data" / "transient" / "documents"
CLEANED_NUMERIC = BASE_DIR / "data" / "cleaned" / "numeric"
CLEANED_DOCUMENTS = BASE_DIR / "data" / "cleaned" / "documents"
CHUNKED = BASE_DIR / "data" / "chunked"
DONE = BASE_DIR / "data" / "done"

for _legacy_path in [
    RAW_NUMERIC,
    RAW_DOCUMENTS,
    TRANS_NUMERIC,
    TRANS_DOCUMENTS,
    CLEANED_NUMERIC,
    CLEANED_DOCUMENTS,
    CHUNKED,
    DONE,
]:
    _legacy_path.mkdir(parents=True, exist_ok=True)
