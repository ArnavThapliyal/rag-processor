"""
Cleaner: remove boilerplate from the parser's blocks and normalise prose.

    blocks, removed = clean_blocks(parse_result.blocks, parse_result.page_count)

A pure function over schema.Block objects. Structure is preserved: page numbers
stay on the blocks (never encoded into text), tables keep their TableData, and
whitespace in tables, lists and code is left alone.

What is removed:
  - blocks matching CLEAN_DENYLIST_PATTERNS ("Page 3 of 12", rule lines, ...);
  - bare page numbers in paged documents;
  - running headers/footers: a short paragraph repeated on a large share of the
    pages of a long document. Headings, tables, lists, captions, footnotes and
    code are never removed this way, and neither are value-like cells ("Nil",
    "N.A.", "12.5%") that legitimately repeat in financial filings.

`removed` is a human-readable log of what was dropped, for the caller to record.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Set, Tuple

from config.settings import (
    CLEAN_DENYLIST_PATTERNS, CLEAN_MAX_BOILERPLATE_CHARS, CLEAN_MIN_PAGES_FOR_REPEAT,
    CLEAN_REPEAT_PAGE_FRACTION,
)
from pipeline.schema import (
    BLOCK_CAPTION, BLOCK_FOOTNOTE, BLOCK_HEADER, BLOCK_PARAGRAPH, BLOCK_TABLE, Block,
)

logger = logging.getLogger(__name__)

# Whitespace is collapsed only in running text; tables, list items and code keep theirs.
_PROSE_TYPES = frozenset({BLOCK_PARAGRAPH, BLOCK_HEADER, BLOCK_CAPTION, BLOCK_FOOTNOTE})

_DENYLIST = [re.compile(p, re.IGNORECASE) for p in CLEAN_DENYLIST_PATTERNS]
_BARE_PAGE_NUMBER = re.compile(r"[-–—\s]*\d{1,4}[-–—\s]*")
# Digits, punctuation and currency only: a value, not a running header.
_VALUE_LIKE = re.compile(r"[\s\d.,:%()+\-–—/₹$€£]*")
_VALUE_WORDS = frozenset({"nil", "n.a.", "n.a", "na", "n/a", "none", "yes", "no", "total", "-do-", "ditto"})
_MAX_LOGGED = 20


def _is_value_like(text: str) -> bool:
    return bool(_VALUE_LIKE.fullmatch(text)) or text.lower() in _VALUE_WORDS


def _repeated_boilerplate(blocks: Sequence[Block], page_count: Optional[int]) -> Set[str]:
    """Short paragraphs that recur on enough pages to be a running header/footer."""
    pages_seen = {b.page_number for b in blocks if b.page_number is not None}
    total_pages = max(page_count or 0, len(pages_seen))
    if total_pages < CLEAN_MIN_PAGES_FOR_REPEAT:
        return set()

    pages_by_text: Dict[str, Set[int]] = defaultdict(set)
    for block in blocks:
        if block.type != BLOCK_PARAGRAPH or block.page_number is None:
            continue
        text = block.content.strip()
        if not text or len(text) > CLEAN_MAX_BOILERPLATE_CHARS or _is_value_like(text):
            continue
        pages_by_text[text].add(block.page_number)

    needed = max(3, CLEAN_REPEAT_PAGE_FRACTION * total_pages)
    return {text for text, pages in pages_by_text.items() if len(pages) >= needed}


def clean_blocks(blocks: Sequence[Block], page_count: Optional[int] = None) -> Tuple[List[Block], List[str]]:
    """Return (kept blocks, removal log). Input blocks are not modified."""
    normalised: List[Block] = []
    for block in blocks:
        if block.type in _PROSE_TYPES:
            block = replace(block, content=re.sub(r"\s+", " ", block.content or "").strip())
        normalised.append(block)

    repeated = _repeated_boilerplate(normalised, page_count)
    kept: List[Block] = []
    removed: Dict[str, int] = defaultdict(int)

    for block in normalised:
        text = (block.content or "").strip()
        if block.type == BLOCK_TABLE:
            if block.table is not None or text:
                kept.append(block)
            continue
        if not text:
            continue
        if block.type == BLOCK_PARAGRAPH:
            if any(p.fullmatch(text) for p in _DENYLIST):
                removed[f"denylist: {text}"] += 1
                continue
            if block.page_number is not None and _BARE_PAGE_NUMBER.fullmatch(text):
                removed["bare page number"] += 1
                continue
            if text in repeated:
                removed[f"running header/footer: {text}"] += 1
                continue
        kept.append(block)

    log = [f"{what} (x{count})" for what, count in list(removed.items())[:_MAX_LOGGED]]
    if len(removed) > _MAX_LOGGED:
        log.append(f"... and {len(removed) - _MAX_LOGGED} more distinct removals")
    if removed:
        logger.info(f"cleaner removed {sum(removed.values())} block(s): {'; '.join(log)}")
    return kept, log
