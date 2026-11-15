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

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from config.settings import EMBEDDING_MODEL, PIPELINE_VERSION, SCRATCH_DIR
from pipeline.metadata import MetadataResolver, build_source_document
from pipeline.schema import SourceDocument, build_record
from pipeline.type_router import HANDLER_DOCUMENT, RoutedFile, open_routed
from pipeline.parser import Parser
from pipeline.cleaner import clean_blocks
from pipeline.chunker import chunk_document, token_counter
from pipeline.embedder import Embedder
from pipeline.vector_store import VectorStore

logger = logging.getLogger(__name__)

_ALREADY_INGESTED = -1


class Indexer:
    def __init__(self):
        self._parser = Parser()
        self._embedder: Embedder | None = None  # loaded on first use
        self._store = VectorStore()
        self._run_id = uuid.uuid4().hex[:12]

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
                        if n == _ALREADY_INGESTED:
                            files_skipped += 1
                        elif n == 0:
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
        if self._store.has_doc(doc.doc_id):
            logger.info(f"[indexer] {doc.source_filename}: already ingested (doc_id {doc.doc_id}); skipping")
            return _ALREADY_INGESTED
        source_filename = doc.source_filename
        result = self._parser.parse(item.path)
        for warning in result.warnings:
            logger.warning(f"[indexer] {source_filename}: {warning}")
        blocks, _removed = clean_blocks(result.blocks, result.page_count)
        new_chunks = chunk_document(blocks, size_fn=token_counter())
        if not new_chunks:
            return 0
        if self._embedder is None:
            self._embedder = Embedder()
        vectors = self._embedder.encode([c.text for c in new_chunks])
        now = datetime.now(timezone.utc).isoformat()
        records = [
            build_record(doc, chunk, vector, ingested_at=now, ingest_run_id=self._run_id,
                         embedding_model=EMBEDDING_MODEL, pipeline_version=PIPELINE_VERSION)
            for chunk, vector in zip(new_chunks, vectors)
        ]
        return self._store.add(records)  # one add per document: atomic
