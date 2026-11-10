#!/usr/bin/env python3
"""
Pipeline orchestration logic.

PipelineRunner does exactly three things, nothing else:
    1. Run the index stage over each input unit (a directory of purchased files).
    2. Log every unit start/end and status.
    3. Validate the metadata dict the stage returns, and aggregate file counts.

It has zero knowledge of *how* indexing works — that lives in indexer.py.

ACTIVE CHAIN:
    index   parse -> clean -> chunk -> embed -> upsert, in memory, per file.

    Acquisition (crawling, collectors) is out of scope: the data is purchased
    and arrives as static files under config.settings.INPUT_DIR.

INPUT UNITS [TRANSITIONAL]:
    A unit is a named directory of files. main.py builds them from the
    immediate subdirectories of the input dir. Until indexer.py is reworked
    (Step 6) the unit name is still passed through as the legacy `symbol`
    value, so name unit directories accordingly if you want `symbol` set.

FAILURE MODEL:
    A unit is failed only if the index stage raises or returns an invalid
    result. Per-file failures inside a unit are isolated by the indexer and
    reported as files_failed; main.py exits non-zero when any are present.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping

from pipeline.indexer import Indexer

logger = logging.getLogger(__name__)

VALID_STATUSES = ("success", "skipped", "no_data")
_COUNTER_KEYS = ("files_indexed", "files_failed", "files_skipped", "chunks_written")


class PipelineRunner:

    def __init__(self) -> None:
        self._indexer = Indexer()

    def run(self, units: Mapping[str, Path]) -> Dict[str, Any]:
        logger.info(f"Starting pipeline for {len(units)} input unit(s)")

        succeeded: List[str] = []
        failed: List[str] = []
        totals = {key: 0 for key in _COUNTER_KEYS}

        for name, path in units.items():
            logger.info(f"[index] [{name}] starting ({path})")
            try:
                raw_result = self._indexer.index_symbol(name, raw_dir=path)
                metadata = self._validate_result(name, raw_result)
                logger.info(f"[index] [{name}] completed: {metadata}")
            except Exception as exc:
                logger.error(f"[index] [{name}] failed: {exc}", exc_info=True)
                failed.append(name)
                continue

            succeeded.append(name)
            for key in _COUNTER_KEYS:
                totals[key] += int(metadata.get(key, 0) or 0)

        logger.info(
            f"Pipeline run complete: {len(succeeded)} succeeded, "
            f"{len(failed)} failed, out of {len(units)} — {totals}"
        )
        return {"total": len(units), "succeeded": succeeded, "failed": failed, **totals}

    @staticmethod
    def _validate_result(name: str, raw: Any) -> Dict[str, Any]:
        """A stage that returns nothing, or a malformed result, is a failure — not a silent success."""
        if not isinstance(raw, dict):
            raise ValueError(f"[{name}] index stage returned {type(raw).__name__}, expected dict")
        status = raw.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"[{name}] index stage returned invalid status {status!r}")
        return raw
