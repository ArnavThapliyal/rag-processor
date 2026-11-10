#!/usr/bin/env python3
"""
Entry point for the RAG processor: ingest purchased files into LanceDB.

    main.py                          # every unit under the default input dir
    main.py --input /path/to/batch   # a different input dir
    main.py --unit VENDOR_A --unit B # only the named units

Input layout: each immediate subdirectory of the input dir is one unit
(a batch of files from one vendor, company, etc.). If the input dir has no
subdirectories it is treated as a single unit.
"""

import sys
import time
import logging
import argparse
from pathlib import Path
from typing import Dict, List

from config.settings import INPUT_DIR, LOG_DIR, ensure_dirs
from pipeline.pipeline import PipelineRunner

logger = logging.getLogger(__name__)

# Directories inside an input dir that are working space, not input units.
_RESERVED_DIRS = {"scratch"}


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


def discover_units(input_dir: Path) -> Dict[str, Path]:
    """Name -> directory for each unit of work under `input_dir`."""
    subdirs = sorted(
        p for p in input_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in _RESERVED_DIRS
    )
    if subdirs:
        loose = [p.name for p in input_dir.iterdir() if p.is_file() and not p.name.startswith(".")]
        if loose:
            logger.warning(
                f"{len(loose)} file(s) directly in {input_dir} are ignored because "
                f"subdirectories exist; move them into a unit directory"
            )
        return {p.name: p for p in subdirs}
    return {input_dir.name: input_dir}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG processor: ingest purchased documents into LanceDB")
    parser.add_argument(
        "--input",
        type=Path,
        default=INPUT_DIR,
        help=f"Directory containing the files to ingest (default: {INPUT_DIR}).",
    )
    parser.add_argument(
        "--unit",
        action="append",
        metavar="NAME",
        help="Only process this unit (subdirectory name). Repeatable.",
    )
    return parser.parse_args()


def print_summary(summary: Dict[str, object]) -> None:
    print(
        f"\n{summary['total']} unit(s) processed: "
        f"{len(summary['succeeded'])} succeeded, {len(summary['failed'])} failed."  # type: ignore[arg-type]
    )
    print(
        f"files: {summary['files_indexed']} indexed, {summary['files_failed']} failed, "
        f"{summary['files_skipped']} skipped | chunks written: {summary['chunks_written']}"
    )
    if summary["failed"]:
        print("Failed units: " + ", ".join(summary["failed"]))  # type: ignore[arg-type]


def main() -> None:
    args = parse_args()  # first, so --help and bad flags have no filesystem side effects
    ensure_dirs()
    setup_logging()

    if not args.input.is_dir():
        print(f"Error: input directory {args.input} does not exist.")
        sys.exit(1)

    units = discover_units(args.input)
    if args.unit:
        missing: List[str] = [name for name in args.unit if name not in units]
        if missing:
            print(f"Error: unit(s) not found under {args.input}: {', '.join(missing)}. "
                  f"Available: {', '.join(units) or '(none)'}")
            sys.exit(1)
        units = {name: units[name] for name in args.unit}

    logger.info(f"Input: {args.input} — {len(units)} unit(s): {', '.join(units)}")

    try:
        summary = PipelineRunner().run(units)
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Pipeline failed to start: {e}", exc_info=True)
        sys.exit(1)

    print_summary(summary)
    sys.exit(1 if summary["failed"] or summary["files_failed"] else 0)


if __name__ == "__main__":
    main()
