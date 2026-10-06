"""Preserve explicit source math formatting; never infer exponents from plain text."""
from html import unescape
import re
import unicodedata


def plain_math_format(text: str) -> str:
    """Comparison key for reconnecting a former flattened source snapshot."""
    text = unescape(re.sub(r"</?(?:sup|sub)>", "", text or "", flags=re.I))

    def math_text(match):
        value = re.sub(r"[\^_]\{([^{}]*)\}", r"\1", match[1])
        return re.sub(r"[\^_]([A-Za-z0-9])", r"\1", value)

    text = re.sub(r"\$\$?(.*?)\$\$?", math_text, text, flags=re.S)
    text = re.sub(r"\\\((.*?)\\\)", math_text, text, flags=re.S)
    text = re.sub(r"\\\[(.*?)\\\]", math_text, text, flags=re.S)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def preserve_option_scripts(options, sources):
    """Restore a uniquely matching labelled option if extraction flattened its scripts.

    This takes the full option from the original formatted source. It does not
    rewrite arbitrary digit sequences or use a model to invent math notation.
    """
    markers = re.compile(r"(?<![A-Za-z0-9])(?:[（(]([A-Z])[）)]|([A-Z])[.．、])\s*")
    candidates = {}
    for source in sources:
        for line in source["text"].splitlines():
            matches = list(markers.finditer(line))
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                text = line[match.end():end].strip()
                label = match[1] or match[2]
                candidates.setdefault((label, plain_math_format(text)), set()).add(text)
    result = []
    for option in options:
        values = candidates.get((option["key"], plain_math_format(option["text"])), set())
        text = next(iter(values)) if len(values) == 1 else ""
        if re.search(r"<(?:sup|sub)>", text, re.I):
            result.append({**option, "text": text})
        else:
            result.append(option)
    return result
