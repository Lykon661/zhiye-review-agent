"""File-based chapter names, numeric ordering, and conservative source roles."""
from pathlib import Path
import re
import unicodedata


def file_title(filename):
    return Path(filename.replace("\\", "/")).stem.strip()[:150] or "未命名资料"


def chinese_number(value):
    digits = {char: number for number, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    if not any(char in "十百千" for char in value):
        return int("".join(str(digits[char]) for char in value))
    total, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
    return total + current


def file_order(filename):
    name = unicodedata.normalize("NFKC", file_title(filename)).casefold()
    name = re.sub(r"第([零〇一二两三四五六七八九十百千]+)(?=[章讲课节])",
                  lambda match: "第" + str(chinese_number(match[1])), name)
    name = re.sub(r"^([零〇一二两三四五六七八九十百千]+)(?=[、.．])",
                  lambda match: str(chinese_number(match[1])), name)
    numbers = tuple(int(value) for value in re.findall(r"\d+", name))
    # The numeric sequence precedes prefixes such as 修改lec, so 2 precedes 10.
    return (0, numbers, name) if numbers else (1, (), name)


SUPPORT_NAME = re.compile(
    r"大纲|提纲|课程说明|课程介绍|课程简介|课程信息|教学计划|课程计划|教学进度|课程安排|"
    r"(?:考试|考核|复习)[\s_-]*(?:范围|要求)|考核说明|考试说明|评分规则|"
    r"syllabus|course[\s_-]*(?:outline|overview|information|schedule|introduction)|grading[\s_-]*(?:policy|criteria)|"
    r"exam[\s_-]*(?:information|instructions|scope|coverage|topics)|review[\s_-]*guide|"
    r"(?:^|[\s_-])outline(?:$|[\s_-])",
    re.I,
)

PRACTICE_NAME = re.compile(
    r"试卷|真题|考卷|模拟卷|练习题|习题集|题库|参考答案|试题|"
    r"(?:^|[\s_-])(?:exam|test|quiz|practice)[\s_-]*(?:paper|questions?)|"
    r"past[\s_-]*papers?|question[\s_-]*bank|answer[\s_-]*key",
    re.I,
)


def document_role(document):
    kind = document["kind"] if isinstance(document, dict) else document.kind
    filename = document["filename"] if isinstance(document, dict) else document.filename
    if kind in ("exam", "exercise", "answer"):
        return "practice"
    title = unicodedata.normalize("NFKC", file_title(filename))
    if SUPPORT_NAME.search(title):
        return "support"
    return "practice" if PRACTICE_NAME.search(title) else "knowledge"


def chapter_role(documents):
    roles = {document_role(doc) for doc in documents}
    if "knowledge" in roles or not roles:
        return "knowledge"
    return "support" if "support" in roles else "practice"
