"""Keep explicitly numbered question bodies intact across paragraphs and pages."""
import re
from .question_images import IMAGE_URL

PRIMARY = re.compile(r"^\s*(\d{1,3})[.．、](?!\d)\s*\S")
PARENTHESIZED = re.compile(r"^\s*[（(](\d{1,3})[）)]\s*\S")
SECTION = re.compile(r"^\s*(?:[一二三四五六七八九十]+[、．.]|\d+[.．、])\s*.*(?:选择题|单选|多选|填空题|判断题|简答题|计算题|证明题|解答题|应用题|select\s+.*choice|multiple\s+choice)", re.I)
CHOICE_SECTION = re.compile(r"选择|单选|多选|select\s+.*choice|multiple\s+choice", re.I)
OPTION = re.compile(r"(?:^|\s)(?:[（(][A-H][）)]|[A-H][.．、])\s*\S", re.M)
ANSWER = re.compile(r"(?:^|\n)\s*(?:参考答案|答案|解析|解答|答|解|answer|solution)\s*[:：]", re.I)
PROMPT = re.compile(r"[?？]|\b(?:scores?|points?)\b|[（(]\s*\d+\s*分|(?:计算|证明|简述|求出|设计|画出|写出|给定|已知|假设)", re.I)
ATTACHMENT = re.compile(r"^_?\d+\.(?:unknown|vsd|emf|wmf)\s*$", re.I)
DIAGRAM = re.compile(r"图|树|电路|figure|diagram|graph|tree|shown|illustrat", re.I)


def question_groups(blocks):
    groups, current = [], []
    section, choice_section = "", False
    number, explicit = "", False
    top_level = False
    page_previews = {}
    native_text_pages = set()
    figure_units = set()
    def unit(locator):
        return tuple((key, locator[key]) for key in ("page", "paragraph", "table", "slide", "shape_path") if key in locator)
    for block in blocks:
        locator = block["locator"]
        if (locator.get("page") is not None and not locator.get("page_preview")
                and not locator.get("recognition") and not locator.get("image_url")
                and not IMAGE_URL.search(block["text"]) and block["text"].strip()):
            native_text_pages.add(locator["page"])
        if block["locator"].get("image_url") or IMAGE_URL.search(block["text"]):
            if block["locator"].get("page_preview"):
                page_previews[block["locator"].get("page")] = block
            else:
                figure_units.add(unit(block["locator"]))
    scan_ocr_pages = {block["locator"]["page"] for block in blocks
                      if block["locator"].get("page") is not None
                      and block["locator"].get("page_preview")
                      and block["locator"].get("recognition") == "ocr"} - native_text_pages

    def flush():
        nonlocal current
        if current:
            groups.append({"sources": current, "number": number, "section": section,
                           "explicit": explicit, "choice_section": choice_section})
            current = []

    for block in blocks:
        if block["locator"].get("page_preview") and block["locator"].get("image_url"):
            # Page images are optional diagram context, not part of the last
            # question's body. Keep page OCR/formulas in the normal text flow.
            continue
        if (block["locator"].get("recognition") in {"ocr", "formula_ocr"} and not block["locator"].get("page_preview")
                and unit(block["locator"]) in figure_units):
            # Diagram labels are already legible in the original figure; OCR's
            # flat list cannot describe their edges or spatial relationships.
            continue
        if (unit(block["locator"]) in figure_units and not block["locator"].get("table")
                and not IMAGE_URL.search(block["text"]) and re.fullmatch(
                    r"(?:[A-Za-z]|\d+)(?:[\s,，;；]+(?:[A-Za-z]|\d+))*", block["text"].strip())):
            # Legacy Word exposes a second native text layer for labels in a
            # drawing. Suppress it only when this exact source unit has a figure.
            continue
        # Tika may append text from embedded Visio/OLE files after the Word body.
        # It has no question position and must not be glued to the last question.
        if ATTACHMENT.fullmatch(block["text"].strip()) or ATTACHMENT.fullmatch(block["locator"].get("heading", "")):
            flush()
            number, explicit = "", False
            continue
        offset = 0
        for line in block["text"].splitlines(keepends=True):
            is_recognition = block["locator"].get("recognition") in {"ocr", "formula_ocr"}
            # On scanned PDF pages, full-page OCR is the question text layer.
            # Supplemental OCR of figures/formulas must not create question boundaries.
            can_number = not is_recognition or (block["locator"].get("page_preview")
                         and block["locator"].get("recognition") == "ocr"
                         and block["locator"].get("page") in scan_ocr_pages)
            header = can_number and len(line) < 250 and SECTION.match(line)
            primary = PRIMARY.match(line) if can_number else None
            sub = PARENTHESIZED.match(line) if can_number else None
            if header:
                flush()
                section = line.strip()
                choice_section = bool(CHOICE_SECTION.search(line))
                number, explicit = "", False
                top_level = False
            elif primary or (sub and (choice_section or not top_level)):
                flush()
                number = (primary or sub)[1]
                explicit = True
                top_level = bool(primary)
                if primary:
                    # An English paper's next top-level question ends its choice
                    # section. Parenthesized subparts of a big question stay inside it.
                    if re.match(r"^\s*\d", section):
                        section = ""
                    choice_section = False
            if current and current[-1]["id"].split(":")[0] == block["id"]:
                current[-1]["text"] += line
            else:
                current.append({"id": f"{block['id']}:{offset}", "text": line, "locator": block["locator"]})
            offset += len(line)
    flush()
    for group in groups:
        if DIAGRAM.search("\n".join(piece["text"] for piece in group["sources"])):
            # Unnumbered scan OCR still needs its original figure available to
            # extraction. Only retain scan pages whose own text refers to a figure.
            pages = {piece["locator"].get("page") for piece in group["sources"]
                     if group["explicit"] or (piece["locator"].get("page") in scan_ocr_pages
                                               and DIAGRAM.search(piece["text"]))}
            existing = {piece["id"].split(":")[0] for piece in group["sources"]}
            for page in sorted(pages - {None}):
                preview = page_previews.get(page)
                if preview and preview["id"] not in existing:
                    group["sources"].append({**preview, "id": preview["id"] + ":0"})
    return groups


def original_other_question(group):
    text = "".join(source["text"] if source["text"].endswith("\n") else source["text"] + "\n"
                   for source in group["sources"]).strip()
    if (not group["explicit"] or group["choice_section"] or OPTION.search(text)
            or ANSWER.search(text) or not PROMPT.search(text)):
        return None
    parts = []
    for source in group["sources"]:
        value = source["text"].strip()
        rows = value.splitlines()
        if source["locator"].get("table") and len(rows) >= 2 and all("\t" in row for row in rows):
            cells = [row.split("\t") for row in rows]
            if len({len(row) for row in cells}) == 1:
                rendered = ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in cells]
                rendered.insert(1, "| " + " | ".join("---" for _ in cells[0]) + " |")
                value = "\n".join(rendered)
        if value:
            parts.append(value)
    text = "\n\n".join(parts)
    code = re.search(r"(?m)^(?:template\s*<|(?:void|int)\s+\w+\s*\(|def\s+\w+\s*\().*", text)
    if code and "```" not in text and "/question-images/" not in text[code.start():]:
        text = text[:code.start()].rstrip() + "\n\n```\n" + text[code.start():].rstrip() + "\n```"
    return {"number": group["number"], "section": group["section"], "question_type": "other",
            "stem": text, "options": [], "locator": group["sources"][0]["locator"],
            "uploaded_answer": "", "uploaded_choices": []}
