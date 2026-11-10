"""
The Processor's data contract, defined once.

  SourceDocument  input to the pipeline: one purchased file plus whatever
                  vendor metadata arrived with it (all of it optional).
  Chunk           in-memory unit produced by the chunker.
  ChunkRecord     the LanceDB row the LLM side reads. Changing it means
                  bumping config.settings.SCHEMA_VERSION.

Vendor metadata is inconsistent by design, so everything not needed to
identify a row is Optional/nullable, and unknown vendor fields are kept in
`metadata_json` rather than dropped. `allowed_roles` is a top-level nullable
column (not buried in metadata_json) because the OPA policy in
tests/rag_authz.rego reads `document.metadata.allowed_roles`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from lancedb.pydantic import LanceModel, Vector

from config.settings import DISTANCE_METRIC, EMBEDDING_DIM

# LanceDB applies the distance metric at index-build and query time, not in the
# table schema. Every create_index()/search() call must use these kwargs so the
# metric (cosine) is defined in exactly one place next to the vector column.
VECTOR_COLUMN = "embedding"
VECTOR_INDEX_KWARGS: Dict[str, Any] = {"metric": DISTANCE_METRIC, "vector_column_name": VECTOR_COLUMN}

# source_type: the detected file format of the original document.
SOURCE_TYPES = frozenset({"pdf", "docx", "pptx", "html", "json", "csv", "xlsx", "xls", "unknown"})

# content_kind: what the chunk holds. Tables are isolated so they can be
# routed to a structured/relational store later without re-parsing.
CONTENT_KIND_PROSE = "prose"
CONTENT_KIND_TABLE = "table"
CONTENT_KINDS = frozenset({CONTENT_KIND_PROSE, CONTENT_KIND_TABLE})

_DOC_ID_HEX_CHARS = 32  # 128 bits of sha256
_CHUNK_ID_HEX_CHARS = 32


class ChunkRecord(LanceModel):
    """One row in the LanceDB table. Order here is the column order."""

    # --- identity (required) ---
    doc_id: str
    chunk_id: str
    chunk_index: int

    # --- content (required) ---
    text: str
    embedding: Vector(EMBEDDING_DIM)  # type: ignore[valid-type]  # fixed-size float32
    content_kind: str  # CONTENT_KINDS
    source_type: str  # SOURCE_TYPES
    source_filename: str

    # --- provenance (required, set by the pipeline) ---
    content_hash: str  # sha256 of `text`; informational, NOT used for dedup
    ingested_at: str  # ISO-8601 UTC
    embedding_model: str
    pipeline_version: str
    ingest_run_id: str

    # --- optional, vendor-supplied or derived; null when unknown ---
    symbol: Optional[str] = None
    source_url: Optional[str] = None
    doc_type: Optional[str] = None
    vendor: Optional[str] = None
    published_at: Optional[str] = None  # ISO-8601 date or datetime as supplied
    section_path: Optional[str] = None  # "H1 > H2 > H3"
    page_range: Optional[str] = None  # "5" or "5-7"; null for page-less formats
    allowed_roles: Optional[List[str]] = None  # null = no restriction declared

    # --- everything else the vendor sent, JSON-encoded ---
    metadata_json: str = "{}"


@dataclass
class SourceDocument:
    """A purchased file plus its optional vendor metadata."""

    path: Path
    doc_id: str
    source_type: str
    source_filename: str
    symbol: Optional[str] = None
    source_url: Optional[str] = None
    doc_type: Optional[str] = None
    vendor: Optional[str] = None
    published_at: Optional[str] = None
    allowed_roles: Optional[List[str]] = None
    extra: Dict[str, Any] = field(default_factory=dict)  # unmapped vendor fields -> metadata_json


@dataclass
class Chunk:
    """Chunker output. Positional provenance is structured, never encoded in text."""

    index: int
    text: str
    content_kind: str = CONTENT_KIND_PROSE
    section_path: Optional[str] = None
    page_range: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)  # e.g. sheet name, row range


# ---- parser output (the IR consumed by the cleaner and chunker) --------------

BLOCK_HEADER = "header"
BLOCK_PARAGRAPH = "paragraph"
BLOCK_LIST_ITEM = "list_item"
BLOCK_TABLE = "table"
BLOCK_CAPTION = "caption"
BLOCK_FOOTNOTE = "footnote"
BLOCK_CODE = "code"
BLOCK_TYPES = frozenset({
    BLOCK_HEADER, BLOCK_PARAGRAPH, BLOCK_LIST_ITEM, BLOCK_TABLE,
    BLOCK_CAPTION, BLOCK_FOOTNOTE, BLOCK_CODE,
})


def _md_cell(value: Any) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


@dataclass
class TableData:
    """A table kept structured so it can be re-chunked by row group or routed to a relational store."""

    header: List[str]
    rows: List[List[str]]

    def to_markdown(self, row_start: int = 0, row_end: Optional[int] = None) -> str:
        """Markdown for rows[row_start:row_end], always including the header row."""
        cols = max(1, len(self.header))
        lines = [
            "| " + " | ".join(_md_cell(h) for h in self.header) + " |",
            "| " + " | ".join("---" for _ in range(cols)) + " |",
        ]
        for row in self.rows[row_start:row_end]:
            cells = list(row) + [""] * (cols - len(row))
            lines.append("| " + " | ".join(_md_cell(c) for c in cells[:cols]) + " |")
        return "\n".join(lines)


@dataclass
class Block:
    """One structural element from the parser, in reading order."""

    type: str  # BLOCK_TYPES
    content: str  # text, or the markdown rendering for tables
    page_number: Optional[int] = None  # None for page-less formats (html, docx)
    level: int = 0  # heading depth 1-6 for BLOCK_HEADER, else 0
    table: Optional[TableData] = None  # set for BLOCK_TABLE
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Dict form used by the pre-refactor cleaner (removed in Step 4)."""
        return {
            "type": self.type,
            "content": self.content,
            "page_number": self.page_number,
            "hierarchical_level": self.level,
        }


@dataclass
class ParseResult:
    blocks: List[Block]
    page_count: Optional[int] = None
    ocr_pages: int = 0
    warnings: List[str] = field(default_factory=list)


def compute_doc_id(path: Path) -> str:
    """Content-addressed id: identical bytes always yield the same doc_id."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:_DOC_ID_HEX_CHARS]


def make_chunk_id(doc_id: str, chunk_index: int, text: str) -> str:
    """
    Deterministic id from doc_id + chunk_index + chunk text.

    Re-running unchanged input reproduces the same ids (idempotent upserts).
    Including the index keeps repeated text inside one document (disclaimers,
    repeated table headers) from colliding; including the text means a changed
    chunk gets a new id instead of silently overwriting its predecessor.
    The \\0 separators make the three parts unambiguous when concatenated.
    """
    payload = f"{doc_id}\0{chunk_index}\0{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:_CHUNK_ID_HEX_CHARS]


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_allowed_roles(value: Any) -> Optional[List[str]]:
    """Accept a list or a comma-separated string from a vendor; return None when absent."""
    if value is None:
        return None
    if isinstance(value, str):
        roles = [r.strip() for r in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        roles = [str(r).strip() for r in value]
    else:
        return None
    roles = [r for r in roles if r]
    return roles or None


def build_record(
    doc: SourceDocument,
    chunk: Chunk,
    embedding: Sequence[float],
    *,
    ingested_at: str,
    ingest_run_id: str,
    embedding_model: str,
    pipeline_version: str,
) -> Dict[str, Any]:
    """Assemble and validate one row. Raises ValueError on a contract violation."""
    if not doc.doc_id:
        raise ValueError("doc_id is required")
    if not chunk.text or not chunk.text.strip():
        raise ValueError(f"chunk {chunk.index} of {doc.doc_id} has empty text")
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(f"embedding has {len(embedding)} dims, expected {EMBEDDING_DIM}")
    if chunk.content_kind not in CONTENT_KINDS:
        raise ValueError(f"unknown content_kind {chunk.content_kind!r}")

    source_type = doc.source_type if doc.source_type in SOURCE_TYPES else "unknown"

    row = ChunkRecord(
        doc_id=doc.doc_id,
        chunk_id=make_chunk_id(doc.doc_id, chunk.index, chunk.text),
        chunk_index=chunk.index,
        text=chunk.text,
        embedding=list(map(float, embedding)),
        content_kind=chunk.content_kind,
        source_type=source_type,
        source_filename=doc.source_filename,
        content_hash=hash_text(chunk.text),
        ingested_at=ingested_at,
        embedding_model=embedding_model,
        pipeline_version=pipeline_version,
        ingest_run_id=ingest_run_id,
        symbol=doc.symbol,
        source_url=doc.source_url,
        doc_type=doc.doc_type,
        vendor=doc.vendor,
        published_at=doc.published_at,
        section_path=chunk.section_path,
        page_range=chunk.page_range,
        allowed_roles=doc.allowed_roles,
        metadata_json=json.dumps({**doc.extra, **chunk.metadata}, default=str, sort_keys=True),
    )
    return row.model_dump()
