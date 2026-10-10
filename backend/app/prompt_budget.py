"""Model-window-aware input estimates; no provider tokenizer is assumed."""
import json
import math
import re


def estimate_tokens(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # CJK often uses more than one token per character; ASCII prose/code is
    # estimated conservatively. This is an estimate, not a provider token count.
    cjk = len(re.findall(r"[\u2e80-\u9fff\uac00-\ud7ff\uf900-\ufaff]", text))
    return math.ceil(cjk * 2 + (len(text) - cjk) / 3)


class InputBudget:
    def __init__(self, config):
        window = max(1024, int(config.context_window))
        output = max(256, int(config.max_tokens))
        framing = max(512, min(4096, window // 16))
        self.limit = max(256, window - output - framing)

    @staticmethod
    def messages_size(messages):
        return estimate_tokens(messages) + 24 * len(messages)

    def size(self, rules, payload):
        return self.messages_size([
            {"role": "system", "content": rules},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ])

    def fits(self, rules, payload, limit=None):
        return self.size(rules, payload) <= (self.limit if limit is None else limit)


def paragraphs_excerpt(text, token_limit, terms=()):
    """Select original paragraphs, rather than cutting facts at a prefix byte cap."""
    if estimate_tokens(text) <= token_limit:
        return text, False
    paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part for part in text.splitlines() if part.strip()]
    normalized = [term.casefold() for term in terms if term]
    ranked = sorted(enumerate(paragraphs), key=lambda item: (
        sum(term in item[1].casefold() for term in normalized),
        bool(re.search(r"复杂度|稳定|原地|空间|\b(?:complexity|stable|unstable|stability|space|auxiliary|worst|average|running time|in.place)\b|O\(", item[1], re.I)),
        -item[0]), reverse=True)
    selected, spent = [], 0
    for index, paragraph in ranked:
        cost = estimate_tokens(paragraph) + 2
        if spent + cost <= token_limit:
            selected.append((index, paragraph))
            spent += cost
    if selected:
        return "\n\n".join(paragraph for _, paragraph in sorted(selected)), True
    # An exceptionally long paragraph is explicitly a partial excerpt.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= token_limit:
            low = middle
        else:
            high = middle - 1
    return text[:low], True
