"""
Indexer

Converts raw documents into LanceDB rows using a single in-memory pipeline.

Pipeline:
    parse -> clean -> chunk -> embed -> upsert

Only two disk operations occur:
    1. Read the raw document.
    2. Commit chunks to LanceDB.

Everything between those steps stays in memory.

Indexing is atomic per file. A failed parse, clean, chunk, or embedding step
never reaches the database. Other files continue processing independently.
"""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from config.settings import SCRATCH_DIR
from pipeline.metadata import MetadataResolver, build_source_document
from pipeline.schema import SourceDocument, make_chunk_id
from pipeline.type_router import HANDLER_DOCUMENT, RoutedFile, open_routed
from pipeline.parser import Parser
from pipeline.cleaner import clean_blocks
from pipeline.chunker import chunk_document, token_counter
from pipeline.embedder import embed_chunks
from pipeline.vector_store import VectorStore

logger = logging.getLogger(__name__)


class Indexer:
    def __init__(self):
        self._parser = Parser()

    def index_files(self, files: Sequence[Path], root: Path) -> Dict[str, Any]:
        """
        Index every file in `files` (found anywhere under `root`). What a file is
        comes only from type_router's sniffing; where it lives only matters for
        locating batch metadata files. One bad file never stops the others.
        """
        resolver = MetadataResolver(root)
        files_indexed = files_failed = files_skipped = chunks_written = 0
        failed_files: List[str] = []

        for file_path in files:
            try:
                # Zip members stay on disk until this with-block exits, i.e. until they are indexed.
                with open_routed(file_path, SCRATCH_DIR) as routed:
                    if not routed:
                        files_skipped += 1
                        continue
                    for item in routed:
                        if item.handler != HANDLER_DOCUMENT:
                            # Tabular files are routed but not yet wired into the index
                            # (Step 6); unsupported ones carry item.reason. Both are counted.
                            logger.info(f"[indexer] skipping {item.display_name}: {item.reason or item.handler}")
                            files_skipped += 1
                            continue
                        try:
                            n = self._index_file(item, build_source_document(item, resolver))
                        except Exception as exc:
                            logger.warning(f"[indexer] failed on {item.display_name}: {exc}", exc_info=True)
                            n = 0
                        if n == 0:
                            files_failed += 1
                            failed_files.append(item.display_name)
                        else:
                            files_indexed += 1
                            chunks_written += n
            except Exception as exc:  # routing/extraction problem for this file only
                logger.warning(f"[indexer] failed to route {file_path}: {exc}", exc_info=True)
                files_failed += 1
                failed_files.append(str(file_path))

        return {
            "status": "success" if files_indexed else "no_data",
            "files_indexed": files_indexed,
            "files_failed": files_failed,
            "files_skipped": files_skipped,
            "chunks_written": chunks_written,
            "failed_files": failed_files,
        }

    def _index_file(self, item: RoutedFile, doc: SourceDocument) -> int:
        # Interim glue: maps the new SourceDocument onto the legacy chunk/metadata dicts
        # the old cleaner/chunker/embedder/store expect. Replaced in Steps 4-6.
        symbol = doc.symbol or ""
        source_filename = doc.source_filename
        result = self._parser.parse(item.path)
        for warning in result.warnings:
            logger.warning(f"[indexer] {source_filename}: {warning}")
        blocks, _removed = clean_blocks(result.blocks, result.page_count)
        new_chunks = chunk_document(blocks, size_fn=token_counter())
        if not new_chunks:
            return 0
        # Legacy embedder/store still take dicts (replaced in Step 5).
        chunks = [
            {"chunk_id": make_chunk_id(doc.doc_id, c.index, c.text), "content": c.text,
             "section_path": c.section_path or "", "page_range": c.page_range or "unknown"}
            for c in new_chunks
        ]

        now = datetime.now(timezone.utc).isoformat()
        for chunk in chunks:
            chunk["content_hash"] = hashlib.sha256(chunk["content"].encode()).hexdigest()
            chunk["source_url"] = doc.source_url or ""
            chunk["page_range"] = chunk.get("page_range", "unknown")
            chunk["metadata"] = {
                "symbol": symbol,
                "source_filename": source_filename,
                "doc_type": doc.doc_type or "unknown",
                "downloaded_at": now,   # embed time, not actual download time
                "section_path": chunk.get("section_path", ""),
            }

        embedded = embed_chunks(chunks)
        VectorStore().run(embedded, symbol)
        return len(embedded)
