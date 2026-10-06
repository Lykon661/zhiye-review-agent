from __future__ import annotations

import pytest

from app.chunking import split_text, token_count


class HFCharacterTokenizer:
    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        result = [ord(character) for character in text]
        return [101, *result, 102] if add_special_tokens else result


class ByteTokenizer:
    """tiktoken-style interface with multiple tokens per Chinese character."""

    def encode(self, text: str, disallowed_special=()) -> bytes:
        return text.encode("utf-8")


def assert_source_coverage(text: str, chunks: list[str]) -> None:
    # Each next chunk must begin within the preceding covered range, and extend it.
    cursor = 0
    previous_start = 0
    for chunk in chunks:
        start = text.find(chunk, previous_start)
        assert start >= 0
        assert start <= cursor
        assert start + len(chunk) > cursor
        previous_start = start + 1
        cursor = start + len(chunk)
    assert cursor == len(text)


@pytest.mark.parametrize("tokenizer", [None, HFCharacterTokenizer(), ByteTokenizer()])
def test_unicode_long_paragraph_stays_inside_budget_and_has_complete_coverage(tokenizer) -> None:
    # The ordinal markers disambiguate overlap and verify that the last tail survives.
    text = "".join(f"{number:04d}中文🙂é缓存映射。" for number in range(200)) + "END末尾"
    chunks = split_text(text, tokenizer=tokenizer, chunk_tokens=90, overlap_tokens=15)

    assert len(chunks) > 1
    assert all(token_count(chunk, tokenizer) <= 90 for chunk in chunks)
    assert chunks[-1].endswith("END末尾")
    assert all("�" not in chunk for chunk in chunks)
    assert_source_coverage(text, chunks)


def test_paragraph_boundaries_are_preferred_and_whitespace_is_preserved() -> None:
    text = "  第一段：" + "缓存" * 10 + "。\n\n" + "第二段：" + "分页" * 12 + "。\n\n第三段保留尾部  "
    chunks = split_text(text, HFCharacterTokenizer(), chunk_tokens=45, overlap_tokens=0)
    assert chunks[0].endswith("\n\n")
    assert "".join(chunks) == text


def test_sentence_boundary_is_preferred_without_dropping_punctuation() -> None:
    text = "第一句内容较长。第二句也有内容。第三句还要继续。第四句终于结束。"
    chunks = split_text(text, HFCharacterTokenizer(), chunk_tokens=18, overlap_tokens=0)
    assert chunks[0].endswith("。")
    assert "".join(chunks) == text


def test_tiktoken_unicode_chunks_if_available() -> None:
    tiktoken = pytest.importorskip("tiktoken")
    # Construct a genuine tiktoken encoder locally; get_encoding may download
    # a vocabulary and would turn this offline unit test into a network test.
    tokenizer = tiktoken.Encoding(
        name="review-offline-byte-encoding",
        pat_str=r"[\s\S]+",
        mergeable_ranks={bytes([value]): value for value in range(256)},
        special_tokens={},
    )
    text = "".join(f"主题{number:03d}：数据库一致性与中文🙂UTF-8。\n" for number in range(120))
    chunks = split_text(text, tokenizer=tokenizer, chunk_tokens=80, overlap_tokens=10)
    assert all(token_count(chunk, tokenizer) <= 80 for chunk in chunks)
    assert_source_coverage(text, chunks)


@pytest.mark.parametrize("chunk_tokens,overlap_tokens", [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 11)])
def test_invalid_budgets_are_rejected(chunk_tokens: int, overlap_tokens: int) -> None:
    with pytest.raises(ValueError):
        split_text("内容", chunk_tokens=chunk_tokens, overlap_tokens=overlap_tokens)


def test_empty_text_and_single_character_over_budget() -> None:
    assert split_text("") == []
    assert split_text(" \n\t") == []
    with pytest.raises(ValueError, match="单个字符"):
        split_text("中", ByteTokenizer(), chunk_tokens=2, overlap_tokens=0)
