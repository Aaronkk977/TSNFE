"""
Collapse Gemini-style ASR junk: the model often emits the same multi‑KB intro/hook
multiple times in one response (not multiple transcribe() calls). Adjacent line/sentence
dedupe alone cannot remove spans separated by tens of thousands of characters.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple


def _is_significant_char(c: str) -> bool:
    if not c or c.isspace():
        return False
    # Keep CJK, letters, digits; drop most punctuation so 「。」 vs 「,」 still align.
    if "\u4e00" <= c <= "\u9fff":
        return True
    return c.isalnum()


def _sig_stream_with_offsets(s: str) -> Tuple[str, List[int]]:
    """Significant-only stream and map index -> original offset in s."""
    chars: List[str] = []
    offsets: List[int] = []
    for i, c in enumerate(s):
        if _is_significant_char(c):
            chars.append(c)
            offsets.append(i)
    return "".join(chars), offsets


def _sync_sig_match_length(s: str, ref: int, dup: int, max_sig_chars: int) -> int:
    """Count matching significant characters when aligning streams starting at ref vs dup."""
    if ref < 0 or dup < 0 or ref >= len(s) or dup >= len(s):
        return 0
    full, _ = _sig_stream_with_offsets(s)
    prefix_ref = _sig_stream_with_offsets(s[:ref])[0]
    prefix_dup = _sig_stream_with_offsets(s[:dup])[0]
    i0, i1 = len(prefix_ref), len(prefix_dup)
    k = 0
    lim = min(len(full) - i0, len(full) - i1, max_sig_chars)
    while k < lim and full[i0 + k] == full[i1 + k]:
        k += 1
    return k


def _byte_length_covering_sig_count(s: str, start: int, sig_count: int) -> int:
    """Original-string length from `start` covering exactly sig_count significant chars."""
    if sig_count <= 0:
        return 0
    got = 0
    i = start
    while i < len(s) and got < sig_count:
        if _is_significant_char(s[i]):
            got += 1
        i += 1
    return i - start


def collapse_far_repeated_anchor_blocks(
    text: str,
    anchor: str,
    *,
    min_sync_significant_chars: int = 2500,
    max_sync_significant_chars: int = 50_000,
) -> Tuple[str, int]:
    """
    If `anchor` appears N>=2 times, and the text following occurrence 1 matches occurrence 0
    for at least min_sync_significant_chars significant characters, delete occurrence 1's block.
    Repeat until stable. Returns (new_text, chars_removed).
    """
    if not text or not anchor:
        return text, 0
    removed_total = 0
    current = text
    while True:
        positions: List[int] = []
        start = 0
        while True:
            i = current.find(anchor, start)
            if i == -1:
                break
            positions.append(i)
            start = i + 1

        if len(positions) < 2:
            break

        ref = positions[0]
        dup = positions[1]
        k = _sync_sig_match_length(current, ref, dup, max_sync_significant_chars)
        if k < min_sync_significant_chars:
            break

        byte_len = _byte_length_covering_sig_count(current, dup, k)
        if byte_len <= 0:
            break

        current = current[:dup] + current[dup + byte_len :]
        removed_total += byte_len

    return current, removed_total


def dedupe_consecutive_lines(text: str) -> str:
    lines = text.split("\n")
    out: List[str] = []
    for line in lines:
        if out and line.strip() and line.strip() == out[-1].strip():
            continue
        out.append(line)
    return "\n".join(out)


def dedupe_adjacent_sentence_like_units(text: str, min_chars: int = 8) -> str:
    if len(text) < min_chars * 2:
        return text
    pieces: List[str] = []
    start = 0
    for m in re.finditer(r"[。！？]", text):
        pieces.append(text[start : m.end()])
        start = m.end()
    if start < len(text):
        pieces.append(text[start:])
    out: List[str] = []
    prev_key: Optional[str] = None
    for p in pieces:
        key = p.strip()
        if len(key) >= min_chars and key and key == prev_key:
            continue
        out.append(p)
        if len(key) >= min_chars:
            prev_key = key
        elif key:
            prev_key = None
    return "".join(out)


def dedupe_transcript_text(
    text: str,
    *,
    far_repeat_anchor: Optional[str] = "我是主持人",
    min_far_repeat_sig_chars: int = 2500,
    do_far_repeat: bool = True,
) -> str:
    """
    Full pipeline: optional anchor-based far repeat removal, then consecutive line/sentence dedupe.
    """
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    if do_far_repeat and far_repeat_anchor:
        t, _ = collapse_far_repeated_anchor_blocks(
            t,
            far_repeat_anchor,
            min_sync_significant_chars=min_far_repeat_sig_chars,
        )
    t = dedupe_consecutive_lines(t)
    t = dedupe_adjacent_sentence_like_units(t)
    return t.strip()
