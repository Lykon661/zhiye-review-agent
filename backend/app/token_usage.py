"""Reply-local provider usage; never share a mutable last-usage counter across chats."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy


_current = ContextVar("reply_token_usage", default=None)
FIELDS = ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens")


def call_stage(messages, action):
    rules = "\n".join(m.get("content", "") for m in messages
                      if m.get("role") == "system" and isinstance(m.get("content"), str))
    if "[TASK:KNOWLEDGE_" in rules:
        return "retrieval"
    if "[TASK:REVIEW_SPACE_PLAN]" in rules or "[TASK:PLAN]" in rules:
        return "planning"
    return "answer" if action in ("complete", "stream") else "other"


def valid_usage(value):
    if not isinstance(value, dict):
        return None
    if any(isinstance(value.get(k), bool) or not isinstance(value.get(k), int) or value[k] < 0 for k in FIELDS):
        return None
    if value["total_tokens"] != value["input_tokens"] + value["output_tokens"]:
        return None
    if value["cache_read_tokens"] + value["cache_write_tokens"] > value["input_tokens"]:
        return None
    reasoning = value.get("reasoning_tokens")
    if reasoning is not None and (isinstance(reasoning, bool) or not isinstance(reasoning, int)
                                   or not 0 <= reasoning <= value["output_tokens"]):
        return None
    return {**{k: value[k] for k in FIELDS}, "reasoning_tokens": reasoning}


class TokenUsage:
    def __init__(self):
        self.calls = []

    def record(self, value, stage):
        self.calls.append({"stage": stage, "usage": valid_usage(value)})

    @staticmethod
    def _totals(calls):
        reported = [c["usage"] for c in calls if c["usage"] is not None]
        status = ("none" if not calls else "unavailable" if not reported
                  else "complete" if len(reported) == len(calls) else "partial")
        totals = {k: sum(u[k] for u in reported) if reported or not calls else None for k in FIELDS}
        reasoning = [u["reasoning_tokens"] for u in reported if u["reasoning_tokens"] is not None]
        return {**totals, "reasoning_tokens": sum(reasoning) if reasoning else None,
                "status": status, "model_calls": len(calls), "reported_calls": len(reported),
                "unreported_calls": len(calls) - len(reported)}

    def snapshot(self):
        calls = deepcopy(self.calls)
        return {**self._totals(calls), "stages": [
            {"stage": stage, **self._totals([c for c in calls if c["stage"] == stage])}
            for stage in dict.fromkeys(c["stage"] for c in calls)]}


@contextmanager
def collect_usage(usage=None):
    usage = usage if usage is not None else TokenUsage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def record_usage(value, stage):
    current = _current.get()
    if current is not None:
        current.record(value, stage)
