"""Model-window-aware input estimates, calibrated by provider-reported usage."""
from collections import deque
import json
import math
import re
from threading import Lock

CJK = re.compile(r"[\u2e80-\u9fff\uac00-\ud7ff\uf900-\ufaff]")


def estimate_tokens(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # Deliberately high for unknown tokenizers (2 per CJK character, 1 per 3
    # other characters); TokenCalibration scales the budget once a model reports usage.
    cjk = len(CJK.findall(text))
    return math.ceil(cjk * 2 + (len(text) - cjk) / 3)


class TokenCalibration:
    """Per-model ratio of reported input tokens to estimate_tokens.

    Most current tokenizers need well under 2 tokens per Chinese character, so
    the raw estimate wastes context. The ratio uses the largest recent sample
    plus a margin, and only small requests or image requests are ignored.
    """
    MIN_ESTIMATE = 400
    MARGIN = 1.1
    BOUNDS = (0.35, 1.5)

    def __init__(self):
        self._lock = Lock()
        self._samples = {}

    @staticmethod
    def key(config):
        get = config.get if isinstance(config, dict) else lambda name: getattr(config, name, "")
        return get("provider"), get("api"), get("base_url"), get("model")

    def record(self, config, messages, reported_input):
        if not isinstance(reported_input, int) or reported_input <= 0:
            return
        if any(not isinstance(message.get("content"), str) for message in messages):
            return  # Image parts are billed differently from their text size.
        estimated = InputBudget.messages_size(messages)
        if estimated < self.MIN_ESTIMATE:
            return
        with self._lock:
            self._samples.setdefault(self.key(config), deque(maxlen=20)).append(reported_input / estimated)

    def ratio(self, config):
        with self._lock:
            samples = self._samples.get(self.key(config))
            if not samples:
                return 1.0
            low, high = self.BOUNDS
            return min(high, max(low, max(samples) * self.MARGIN))


CALIBRATION = TokenCalibration()


class InputBudget:
    def __init__(self, config):
        window = max(1024, int(config.context_window))
        output = max(256, int(config.max_tokens))
        framing = max(512, min(4096, window // 16))
        # The limit is in estimate units: real tokens are about ratio * estimate.
        self.limit = max(256, int((window - output - framing) / CALIBRATION.ratio(config)))

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
