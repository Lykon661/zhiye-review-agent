"""Conservative local answer parsing and inexpensive candidate retrieval."""
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .lexical import lexical_score, normalize, query_terms


ANSWER_MARK = re.compile(r"(?:参考|标准|正确)?答案\s*[:：]|解答\s*[:：]|(?:^|\n)\s*(?:答|解)\s*[:：]|answer\s*[:：]", re.I)
NUMBERED = re.compile(r"(?m)^\s*(?:第\s*(\d+)\s*题\s*[:：.、]?|[（(]?(\d+)[)）.、．])\s*")
PAIR = re.compile(r"(?<![\w])(?:第\s*)?(\d+)\s*(?:题\s*[:：]?|[.、:：)）]|\s)\s*([A-Z](?:[A-Z]|\s*[,，、/]\s*[A-Z])*)(?=$|[\s;；|])", re.I)
ANSWER_TITLE = re.compile(r"(?mi)^\s*(?:(?:参考|标准|正确)?答案(?:与解析)?|参考解析|answer\s*key|solutions?)\s*[:：]?\s*$")
ANSWER_FILENAME = re.compile(r"答案|解答|解析|answers?|solutions?", re.I)
CHOICE_OPTION = re.compile(r"(?<!\w)(?:[（(]([A-Za-zＡ-Ｚａ-ｚ])[)）]|([A-Za-zＡ-Ｚａ-ｚ])[.．、:：)）])\s*")
INLINE_CHOICE = re.compile(r"(?<!\w)[（(]\s*([A-Za-zＡ-Ｚａ-ｚ][A-Za-zＡ-Ｚａ-ｚ\s,，、/]*)\s*[)）]")


def contains_answers(blocks):
    text = unicodedata.normalize("NFKC", "\n".join(b["text"] for b in blocks))
    if (ANSWER_MARK.search(text) or ANSWER_TITLE.search(text)
            or re.search(r"(?m)^\s*(?:题号|题目编号|question\s*(?:no\.?|number)).*\n\s*(?:参考|标准|正确)?答案", text, re.I)):
        return True
    if next(inline_choice_answers(text), None) is not None:
        return True
    count = 0
    for line in text.splitlines():
        table = line.replace("|", " ")
        matches = list(PAIR.finditer(table))
        if matches and re.fullmatch(r"[\s,，;；]*", PAIR.sub("", table)):
            count += len(matches)
    return count >= 2


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def number(text):
    value = unicodedata.normalize("NFKC", str(text)).strip()
    match = re.fullmatch(r"(?:第\s*)?[（(]?(\d+)[)）.、]?\s*(?:题)?", value)
    return str(int(match[1])) if match else None


def family(text):
    value = normalize(text)
    if re.search(r"多选|多项选择|multiple[ -]*answers?|select\s*all", value):
        return "multiple_choice"
    if re.search(r"单选|单项选择|single[ -]*choice", value):
        return "single_choice"
    if re.search(r"选择题|选择|choice", value):
        return "choice"
    for kind, pattern in (("fill", r"填空|fill.*blank"), ("judgment", r"判断|true.*false"),
                          ("calculation", r"计算|calculation"), ("short", r"简答|问答|short.*answer"),
                          ("proof", r"证明|proof")):
        if re.search(pattern, value):
            return kind
    return None


def section_order(text):
    match = re.match(r"\s*([一二三四五六七八九十]+|\d+|[IVX]+)[、.)]\s*",
                     unicodedata.normalize("NFKC", text), re.I)
    if not match:
        return None
    value = match[1].upper()
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    if value.isdigit():
        return str(int(value))
    if "十" in value:
        left, right = value.split("十", 1)
        return str((digits.get(left, 1) if left else 1) * 10 + digits.get(right, 0))
    if value in digits:
        return str(digits[value])
    roman = {"I": 1, "V": 5, "X": 10}
    return str(sum(-roman[char] if i + 1 < len(value) and roman[char] < roman[value[i + 1]] else roman[char]
                   for i, char in enumerate(value))) if all(char in roman for char in value) else None


def section_heading(text):
    if len(text) >= 140:
        return False
    value = re.sub(r"^\s*(?:[一二三四五六七八九十]+|\d+|[IVX]+)[、.)]\s*", "",
                   unicodedata.normalize("NFKC", text), flags=re.I)
    return bool(re.match(r"(?:单选|多选|单项选择|多项选择|选择题|填空|判断|简答|问答|计算|证明|"
                         r"single[ -]choice|multiple[ -]choice|multiple[ -]answers?|select\s+(?:the\s+)?correct\s+choice|true\s*(?:/|or)\s*false|"
                         r"short[ -]answer|fill.*blank|calculation|proof)", value.strip(), re.I))


def compatible(entry_family, question_family):
    if not entry_family or not question_family:
        return True
    if "choice" in (entry_family, question_family):
        return entry_family in {"choice", "single_choice", "multiple_choice"} and question_family in {"choice", "single_choice", "multiple_choice"}
    return entry_family == question_family


def paper_name(filename):
    value = normalize(Path(filename).stem)
    value = re.sub(r"参考答案|标准答案|答案与解析|参考解析|练习题|试题|题目|试卷|答案|解析|answer\s*key|questions?|answers?|solutions?|exam|paper", "", value)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", value)


def paired(context, source):
    doc = context["doc"]
    if source.id == doc.id:
        return True
    if source.id == context.get("answer_document_id"):
        return True  # The user explicitly associated this answer file with the paper.
    if doc.year is not None and source.year is not None and doc.year != source.year:
        return False
    title = paper_name(doc.filename)
    # File counts and years alone cannot establish a paper's identity.
    return title == paper_name(source.filename) and len(re.sub(r"[0-9]", "", title)) >= 3


def labels(text):
    value = unicodedata.normalize("NFKC", text).strip().upper()
    if not re.fullmatch(r"[A-Z](?:[A-Z]|\s*[,、/]\s*[A-Z])*", value):
        return []
    return sorted(set(re.findall(r"[A-Z]", value)))


def answer_labels(text):
    marker = ANSWER_MARK.search(text)
    value = text[marker.end():] if marker else text
    match = re.match(r"\s*[(（\[]?([A-Z](?:[A-Z]|\s*[,，、/]\s*[A-Z])*)[)）\]]?"
                     r"(?=$|[。.;；:：\n\r]|[ \t]+(?:解析|说明|理由|解释|因为|本题|故|因此|正确|错误|because|explanation))",
                     value.strip(), re.I)
    return labels(match[1]) if match else []


def inline_choice_answers(text):
    """Read filled answer slots only before a complete sequence of choice options."""
    starts = list(NUMBERED.finditer(text))
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        body = text[start.end():end].strip()
        markers = list(CHOICE_OPTION.finditer(body))
        for first, marker in enumerate(markers):
            if labels(marker[1] or marker[2]) != ["A"]:
                continue
            options = markers[first:]
            keys = [labels(option[1] or option[2])[0] for option in options]
            if len(keys) < 2 or keys != list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"[:len(keys)]):
                continue
            stem = body[:marker.start()].strip()
            slots = []
            for slot in INLINE_CHOICE.finditer(stem):
                selected = labels(slot[1])
                # Padding or a trailing answer blank distinguishes a filled slot
                # from ordinary parenthesized variables in the question text.
                padded = bool(re.match(r"[（(]\s+", slot[0]) or re.search(r"\s+[)）]$", slot[0]))
                trailing = not stem[slot.end():].strip(" \t\r\n.,。;；:：?!？！")
                if selected and set(selected).issubset(keys) and (padded or trailing):
                    slots.append((slot, selected))
            if len(slots) != 1:
                continue
            slot, selected = slots[0]
            option_text = {key: body[option.end():options[i + 1].start() if i + 1 < len(options) else len(body)].strip()
                           for i, (key, option) in enumerate(zip(keys, options))}
            if any(not value for value in option_text.values()):
                continue
            before = text[:start.start()].splitlines()
            heading = next((line.strip() for line in reversed(before) if section_heading(line)), "")
            yield {"number": str(int(start[1] or start[2])), "section": heading,
                   "question_text": stem, "text": body, "answer": slot[0],
                   "choices": tuple(selected), "option_text": option_text, "inline_answer": True}
            break


def inline_stem(text):
    text = unicodedata.normalize("NFKC", text).strip()
    numbered = NUMBERED.match(text)
    if numbered:
        text = text[numbered.end():]
    text = INLINE_CHOICE.sub("", text)
    return compact(re.sub(r"\(\s*\)", "", text))


@dataclass
class Candidate:
    id: str
    source: object
    text: str
    question_text: str = ""
    answer: str = ""
    number: str | None = None
    section: str = ""
    choices: tuple = ()
    option_text: dict = field(default_factory=dict)
    inline_answer: bool = False

    def payload(self):
        # Atomic entries already contain the exact uploaded answer. The model
        # selects their IDs; only unstructured snippets need quoted spans.
        # Keep every question condition. Only the answer preview is shortened;
        # selecting this ID still returns the full saved original answer.
        preview = (self.question_text + "\n" + self.answer[:700]).strip() if self.answer else self.text
        return {"id": self.id, "filename": self.source.filename, "year": self.source.year,
                "number": self.number, "section": self.section,
                "text": preview, "atomic": bool(self.answer), "choices": list(self.choices)}


class LocalAnswerMatcher:
    def __init__(self, context):
        self.context = context
        self.entries = []
        self.snippets = []
        self.serial = 0
        self.question_counts = Counter(number(q.number) for q in context["questions"])
        for source, blocks in context["candidates"]:
            text = "\n".join(b["text"] for b in blocks)
            self.parse(source, text)
            # Keep raw windows as a fallback for OCR/table layouts the parser
            # cannot safely interpret. Retrieval, not a model, ranks them.
            for start in range(0, len(text), 1000):
                window = text[max(0, start - 150):start + 1400]
                if window.strip():
                    self.snippets.append(self.make(source, window))

    def make(self, source, text, **kwargs):
        self.serial += 1
        return Candidate(id=f"a{self.serial}", source=source, text=text, **kwargs)

    def parse(self, source, text):
        section, pending = "", None
        for item in inline_choice_answers(text):
            self.entries.append(self.make(source, **item))
        confirmed_source = source.kind == "answer" or source.id == self.context.get("answer_document_id")
        # Answer-table rows: 1.A 2.C, 1 A / 2 C, and numbered columns
        # followed by an explicitly labelled answer row.
        for raw in text.splitlines():
            line = unicodedata.normalize("NFKC", raw).strip()
            if len(line) < 140 and ANSWER_TITLE.fullmatch(line):
                continue
            if section_heading(line):
                section, pending = line, None
                continue
            cleaned = line.replace("|", " ")
            if re.match(r"^(?:题号|题目编号|question\s*(?:no\.?|number))", cleaned.strip(), re.I):
                pending = [str(int(n)) for n in re.findall(r"\d+", cleaned)]
                continue
            answer_row = re.match(r"^\s*(?:参考|标准|正确)?答案\s*[:：]?\s*(.*)$", cleaned)
            if pending and answer_row:
                cells = re.split(r"\s+", answer_row[1].strip())
                values = [labels(cell) for cell in cells]
                if len(values) == len(pending) and all(values):
                    for identity, value in zip(pending, values):
                        self.entries.append(self.make(source, raw, number=identity, section=section,
                                                      answer=raw, choices=tuple(value)))
                pending = None
                continue
            if re.fullmatch(r"[\s:|\-]+", line):
                continue
            pending = None
            table = re.sub(r"^(?:参考|标准|正确)?答案\s*[:：]\s*", "", cleaned)
            matches = list(PAIR.finditer(table))
            remainder = PAIR.sub("", table)
            if matches and re.fullmatch(r"[\s,，;；|]*", remainder):
                for item in matches:
                    # Store the actual matched text, without synthesizing a quote.
                    self.entries.append(self.make(source, raw, number=str(int(item[1])), section=section,
                                                  answer=item[0].strip(), choices=tuple(labels(item[2]))))

        # Repeated question stems followed by an explicit answer marker. Only
        # starts whose immediate segment has a marker become question boundaries;
        # numbered calculation steps therefore stay inside the saved answer.
        starts = list(NUMBERED.finditer(text))
        valid = []
        answer_headers = [m.start() for m in ANSWER_TITLE.finditer(text)]
        for index, start in enumerate(starts):
            end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
            body = text[start.end():end]
            bare = answer_labels(body) and (confirmed_source or any(pos < start.start() for pos in answer_headers))
            if ANSWER_MARK.search(body) or bare:
                valid.append(start)
        for index, start in enumerate(valid):
            end = valid[index + 1].start() if index + 1 < len(valid) else len(text)
            body = text[start.end():end].strip()
            marker = ANSWER_MARK.search(body)
            bare = answer_labels(body) and (confirmed_source or any(pos < start.start() for pos in answer_headers))
            if marker is None and not bare:
                continue
            # Don't carry a later section/header into the preceding solution.
            answer_start = 0 if bare else marker.start()
            after = 0 if bare else marker.end()
            boundary = re.search(r"(?m)^\s*[一二三四五六七八九十]+[、.．]\s*.*(?:选择|填空|判断|简答|计算|证明)", body[after:])
            if boundary:
                body = body[:after + boundary.start()].rstrip()
            answer = body[answer_start:].strip()
            before = text[:start.start()].splitlines()
            heading = next((line.strip() for line in reversed(before) if section_heading(line)), "")
            self.entries.append(self.make(source, body, number=str(int(start[1] or start[2])), section=heading,
                                          question_text=body[:answer_start].strip(), answer=answer,
                                          choices=tuple(answer_labels(answer))))

    def question_family(self, q):
        return q.question_type if q.question_type != "other" else family(q.section)

    def number_match(self, q, item):
        identity = number(q.number)
        if identity is None or identity != item.number or not paired(self.context, item.source):
            return False
        kind = family(item.section)
        if not compatible(kind, self.question_family(q)):
            return False
        if section_order(item.section) and section_order(q.section) and section_order(item.section) != section_order(q.section):
            return False
        if not item.section:
            return self.question_counts[identity] == 1
        same = [other for other in self.context["questions"] if number(other.number) == identity
                and compatible(kind, self.question_family(other))
                and (not section_order(item.section) or not section_order(other.section)
                     or section_order(item.section) == section_order(other.section))]
        return len(same) == 1

    def stem_match(self, q, item):
        if item.inline_answer:
            return (q.question_type != "other" and number(q.number) == item.number
                    and inline_stem(q.stem) == inline_stem(item.question_text)
                    and {key: compact(value) for key, value in item.option_text.items()}
                    == {o["key"]: compact(o["text"]) for o in q.options})
        stem, candidate = compact(q.stem), compact(item.question_text)
        if (not stem or (len(stem) < 12 and not self.number_match(q, item))
                or not candidate.startswith(stem)):
            return False
        if q.question_type == "other":
            return candidate == stem
        tail = item.question_text
        matches = list(re.finditer(r"(?<![A-Za-z0-9])([A-Z])[.．、:：)）]\s*", tail))
        options = {m[1]: compact(tail[m.end():matches[i + 1].start() if i + 1 < len(matches) else len(tail)])
                   for i, m in enumerate(matches)}
        return options == {o["key"]: compact(o["text"]) for o in q.options}

    def resolution(self, q, item):
        if not item.answer:
            return None
        selected = list(item.choices)
        if q.question_type == "other" and selected:
            return None
        if q.question_type != "other":
            keys = {o["key"] for o in q.options}
            if not selected or not set(selected).issubset(keys) or q.question_type == "single_choice" and len(selected) != 1:
                return None
        return {"answer": item.answer, "choices": selected if q.question_type != "other" else [],
                "origin": "embedded" if item.source.id == self.context["doc"].id else "uploaded",
                "source": item.source.filename, "sources": []}

    def local_matches(self, q):
        found, ambiguous = [], False
        for item in self.entries:
            by_number, by_stem = self.number_match(q, item), self.stem_match(q, item)
            if by_stem or (not item.question_text and by_number):
                result = self.resolution(q, item)
                if result:
                    found.append(result)
                else:
                    ambiguous = True
            elif by_number and item.question_text:
                # A corresponding paper repeats a different stem/options. A
                # number alone must not override that contradictory evidence.
                ambiguous = True
        if ambiguous and len({tuple(item["choices"]) if q.question_type != "other" else compact(item["answer"])
                              for item in found}) <= 1:
            return []
        return found

    def candidates(self, q, limit=3):
        original = normalize(q.stem)
        grams = {original[i:i + 2] for i in range(len(original) - 1)}
        terms, _ = query_terms(q.stem)
        ranked = []
        for item in self.entries + self.snippets:
            # Exact full-stem matches were already handled locally. An LLM
            # cannot establish paper identity from an unrelated answer table.
            if not paired(self.context, item.source):
                continue
            text = normalize(item.question_text or item.text)
            other = {text[i:i + 2] for i in range(len(text) - 1)}
            overlap = len(grams & other) / max(1, len(grams))
            score = overlap * 0.65 + lexical_score(text, terms) * 0.2
            if paired(self.context, item.source):
                score += 0.08
                if item.number and item.number == number(q.number):
                    score += 0.45 if compatible(family(item.section), self.question_family(q)) else 0
            elif item.number and item.number == number(q.number):
                score += 0.04
            elif not item.number and number(q.number) and re.search(
                    rf"(?<!\d){re.escape(number(q.number))}\s*[.、:：)）]", item.text):
                score += 0.04
            if score > 0:
                ranked.append((score, item))
        ranked.sort(key=lambda row: (-row[0], not bool(row[1].answer), row[1].id))
        result, seen = [], set()
        for _, item in ranked:
            identity = (item.source.id, compact(item.text), item.number)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(item)
            if len(result) >= limit:
                break
        if not result:
            # Unrelated text gets no model call. Bare number-only answers with
            # uncertain paper identity still enter retrieval via their number.
            return []
        return result
