#!/usr/bin/env python3
"""
Entry point for the RAG processor: ingest purchased files into LanceDB.

    main.py [ROOT]

ROOT is any directory (default: config.settings.INPUT_DIR), or a single file.
It is crawled recursively; there is no required folder structure and folder
names are ignored. File types are detected from content by pipeline/type_router.
Optional metadata files (*.meta.json, metadata.json, manifest.json) are read
for vendor metadata and are never ingested themselves.
"""

import sys
import time
import logging
import argparse
from pathlib import Path
from typing import Any, Dict

from config.settings import INPUT_DIR, LOG_DIR, ensure_dirs
from pipeline.pipeline import PipelineRunner

logger = logging.getLogger(__name__)

_MAX_FAILED_LISTED = 20


def setup_logging() -> None:
    """
    Configure the ROOT logger once, in one place. Every module's
    `logging.getLogger(__name__)` propagates to root, so handlers attached
    here via basicConfig cover the whole pipeline.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    log_formatter.converter = time.gmtime  # UTC timestamps

    file_handler = logging.FileHandler(LOG_DIR / "pipeline.log")
    file_handler.setFormatter(log_formatter)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(log_formatter)

    logging.basicConfig(level=logging.INFO, handlers=[file_handler, stdout_handler])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG processor: ingest purchased documents into LanceDB")
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=INPUT_DIR,
        help=f"Directory to crawl recursively, or a single file (default: {INPUT_DIR}).",
    )
    return parser.parse_args()


def print_summary(summary: Dict[str, Any]) -> None:
    print(
        f"\n{summary['files_found']} file(s) found under {summary['root']}: "
        f"{summary['files_indexed']} indexed, {summary['files_failed']} failed, "
        f"{summary['files_skipped']} skipped | chunks written: {summary['chunks_written']}"
    )
    failed = summary["failed_files"]
    if failed:
        shown = ", ".join(failed[:_MAX_FAILED_LISTED])
        more = f" (+{len(failed) - _MAX_FAILED_LISTED} more)" if len(failed) > _MAX_FAILED_LISTED else ""
        print(f"Failed: {shown}{more}")


def main() -> None:
    args = parse_args()  # first, so --help and bad flags have no filesystem side effects
    if not args.root.exists():
        print(f"Error: {args.root} does not exist.")
        sys.exit(1)

    ensure_dirs()
    setup_logging()

    try:
        summary = PipelineRunner().run(args.root)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)

    print_summary(summary)
    sys.exit(1 if summary["files_failed"] else 0)


if __name__ == "__main__":
    main()
