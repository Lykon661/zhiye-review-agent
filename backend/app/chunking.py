"""Source-preserving text chunks constrained by the embedding tokenizer.

Pass the embedding model's HF tokenizer in production. Without a tokenizer the
fallback counts each Python character as one unit; it is a conservative Chinese
character splitter, not a claim of an actual model token count.
"""

from __future__ import annotations

import logging
import re
from typing import Any


logger = logging.getLogger(__name__)


def token_count(text: str, tokenizer: Any = None) -> int:
    if tokenizer is None:
        return len(text)
    try:
        encoded = tokenizer.encode(text, add_special_tokens=False)
    except TypeError:
        # tiktoken does not accept the HF special-token keyword.
        try:
            encoded = tokenizer.encode(text, disallowed_special=())
        except TypeError:
            encoded = tokenizer.encode(text)
    return len(encoded)


def _max_prefix_end(text: str, start: int, budget: int, tokenizer: Any) -> int:
    """Find a bounded prefix without decoding tokens or replacing Unicode text."""
    total = len(text)
    low = start
    high = min(total, start + max(32, budget * 4))
    while token_count(text[start:high], tokenizer) <= budget:
        low = high
        if high == total:
            return high
        high = min(total, start + (high - start) * 2)
    while low + 1 < high:
        middle = (low + high) // 2
        if token_count(text[start:middle], tokenizer) <= budget:
            low = middle
        else:
            high = middle
    # Token counts can vary slightly at a merge boundary. This final check is
    # the hard invariant; binary search is used only to choose a good prefix.
    while low > start and token_count(text[start:low], tokenizer) > budget:
        low -= 1
    if low == start:
        raise ValueError("单个字符的 token 数已超过分块上限，请增大 chunk_tokens。")
    return low


def _preferred_end(text: str, start: int, end: int) -> int:
    if end == len(text):
        return end
    segment = text[start:end]
    # Avoid tiny chunks just because an early short heading has a newline.
    minimum = max(1, len(segment) // 2)
    for pattern in (r"\n\s*\n", r"\n", r"[。！？!?；;]|\.(?=\s)"):
        candidates = [match.end() for match in re.finditer(pattern, segment) if match.end() >= minimum]
        if candidates:
            return start + candidates[-1]
    return end


def _overlap_start(text: str, start: int, end: int, budget: int, tokenizer: Any) -> int:
    if budget == 0:
        return end
    # Seek a suffix in the original string, never a lossy token.decode result.
    low, high = start, end
    while low < high:
        middle = (low + high) // 2
        if token_count(text[middle:end], tokenizer) <= budget:
            high = middle
        else:
            low = middle + 1
    while low < end and token_count(text[low:end], tokenizer) > budget:
        low += 1
    return max(start + 1, low)


def split_text(
    text: str,
    tokenizer: Any = None,
    chunk_tokens: int = 300,
    overlap_tokens: int = 50,
) -> list[str]:
    """Split one source block independently; every non-space character survives.

    Chunks are original character slices, so token decoding cannot corrupt
    Chinese characters or whitespace. Adjacent chunks overlap by at most the
    requested token budget and the algorithm always advances.
    """
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens 必须为正整数。")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens 必须大于等于 0 且小于 chunk_tokens。")
    if not text or not text.strip():
        return []
    if tokenizer is None:
        logger.warning("未提供 embedding tokenizer，分块仅使用字符数 fallback；生产入库应传入模型 tokenizer。")
    chunks: list[str] = []
    start = 0
    while start < len(text):
        maximum_end = _max_prefix_end(text, start, chunk_tokens, tokenizer)
        end = _preferred_end(text, start, maximum_end)
        # Choosing a textual boundary can change BPE merges, so check again.
        while end > start and token_count(text[start:end], tokenizer) > chunk_tokens:
            end -= 1
        if end == start:
            raise ValueError("无法在 token 上限内分块，请检查 tokenizer 或增大 chunk_tokens。")
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = _overlap_start(text, start, end, overlap_tokens, tokenizer)
    return chunks
