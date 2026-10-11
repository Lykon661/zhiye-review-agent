"""Source-preserving page/section views and contextual embedding passages."""
from collections import Counter, OrderedDict
import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

from .chunking import split_text, token_count
from .lexical import normalize
from .models import RetrievalPassage, RetrievalUnit

UNIT_POLICY = "page_section_context_v2"


def document_signature(blocks):
    return hashlib.sha256(json.dumps([
        [b.id, b.order, b.text, b.heading, b.block_type, b.locator, b.chapter_id] for b in blocks
    ], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def clean_title(title):
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"^(?:analysis of|properties of|characteristics of|the steps of|example of)\s+", "", title, flags=re.I)
    title = re.sub(r"(?:\s*[:：]\s*(?:analysis|properties|characteristics)|\s+(?:analysis|properties|characteristics|example))$", "", title, flags=re.I)
    return re.sub(r"\s*[（(](?:[ivxlcdm]+|\d+)[）)]$", "", title, flags=re.I).strip()


def _title(blocks, footer_texts, filename):
    explicit = next((b.heading for b in blocks if b.heading and normalize(b.heading) not in footer_texts), None)
    if explicit:
        return explicit.strip()
    for block in blocks:
        value = block.text.strip()
        if (3 <= len(value) <= 110 and "\n" not in value and normalize(value) not in footer_texts
                and not re.search(r"[{};=]|^\d[\d .-]*$|\blec(?:ture)?[\s.-]*\d", value, re.I)):
            return value
    return filename


def build_units(document, blocks, tokenizer=None):
    """PPT/PDF by physical page; headed Word/text by bounded source sections."""
    ordered = sorted(blocks, key=lambda b: b.order)
    counts = Counter(normalize(b.text) for b in ordered if len(b.text) < 160)
    page_count = len({(key, b.locator[key]) for b in ordered for key in ("slide", "page") if key in b.locator})
    # Frequency alone cannot identify footers: a whole lecture may repeat
    # "Heap Sort" on every slide. Keep repeated knowledge titles and formulas.
    footer_texts = {text for text, count in counts.items()
                   if page_count >= 6 and count >= max(4, page_count * 0.55)
                   and re.search(r"©|copyright|all rights reserved|https?://|www\.|\blec(?:ture)?[\s.-]*\d|大学|学院|\b(?:university|department|college)\b", text, re.I)}
    groups = OrderedDict()
    section, size, previous_heading = 0, 0, None
    for block in ordered:
        page = next(((key, block.locator[key]) for key in ("slide", "page") if key in block.locator), None)
        if page:
            key = (page[0], page[1], block.chapter_id or "")
        else:
            if (block.block_type == "heading" or (block.heading and block.heading != previous_heading)
                    or size >= 12000):
                section += 1
                size = 0
            key = ("section", section, block.chapter_id or "")
            size += len(block.text)
            previous_heading = block.heading
        groups.setdefault(key, []).append(block)
    units, passages = [], []
    for position, (key, members) in enumerate(groups.items()):
        useful = [b for b in members if normalize(b.text) not in footer_texts and b.text.strip()]
        if not useful:
            continue
        title = _title(useful, footer_texts, document.filename)
        texts, seen = [], set()
        for block in useful:
            text = block.text.strip()
            if text not in seen:
                texts.append(text)
                seen.add(text)
        body = "\n\n".join(texts)
        unit_key = json.dumps(key, ensure_ascii=False)
        identity = uuid5(NAMESPACE_URL, UNIT_POLICY + ":" + document.id + ":" + unit_key).hex
        locator = {"filename": document.filename}
        if key[0] in ("slide", "page"):
            locator[key[0]] = key[1]
            locator["label"] = f"第 {key[1]} 张幻灯片" if key[0] == "slide" else f"第 {key[1]} 页"
        else:
            locator.update(section=key[1], label=f"小节：{title}")
        prefix = f"资料：{document.filename}\n本页/小节标题：{title}\n"
        units.append(RetrievalUnit(id=identity, course_id=document.course_id, document_id=document.id,
            chapter_id=useful[0].chapter_id, unit_key=unit_key, position=position, title=title,
            text=body, index_text=prefix + body, block_ids=[b.id for b in useful], locator=locator))
        # Leave room for the title in the CPU runtime's 512-token window. Titles
        # and filenames contextualize every passage, not just the first one.
        prefix = f"标题：{title[:110]}\n资料：{document.filename[:90]}\n"
        while token_count(prefix, tokenizer) > 170:
            prefix = prefix[:-1]
        passage_tokens = max(120, min(300, 470 - token_count(prefix, tokenizer)))
        for offset, text in enumerate(split_text(body, tokenizer=tokenizer, chunk_tokens=passage_tokens, overlap_tokens=35)):
            passage_id = uuid5(NAMESPACE_URL, identity + ":" + str(offset)).hex
            passages.append(RetrievalPassage(id=passage_id, unit_id=identity, document_id=document.id,
                position=offset, text=prefix + text))
    return units, passages
