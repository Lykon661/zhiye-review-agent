"""Exact term matching and coverage hints; multilingual vectors use the original query."""
import re
import unicodedata


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def term_pattern(term):
    term = normalize(term)
    # English "supervised" must not match "unsupervised"; preserve Chinese phrases.
    left = r"(?<![a-z0-9])" if term and term[0].isascii() and term[0].isalnum() else ""
    right = r"(?![a-z0-9])" if term and term[-1].isascii() and term[-1].isalnum() else ""
    return left + re.escape(term) + right


def retrieval_topic(query):
    """Remove the request wording, keeping the actual subject for retrieval."""
    topic = normalize(query).strip("*_` ")
    topic = re.sub(r"^(?:(?:请|帮我|带我|给我|我想|我要|想要)\s*)+", "", topic)
    topic = re.sub(r"^(?:(?:复习一下|复习|学习一下|学习|列举|对比一下|对比|比较一下|比较|总结一下|梳理一下|一下|各种|各类|各个|每种|讲解一下|讲解|解释一下|解释|简单说明|什么是|什么叫|what is |what are |explain |define )\s*)+", "", topic)
    return re.sub(r"(?:(?:的)?(?:相关知识|相关内容)|是什么|是什么意思|的定义|的概念|[?？。！!])+$", "", topic).strip()


def overview_request(query):
    """A comparison/overview needs coverage, rather than a few similar chunks."""
    return bool(re.search(r"列举|对比|比较|总结|梳理|汇总|总览|各种|各类|各个|每种|所有|全部|\b(?:compare|comparison|overview|summarize|all types|every)\b", normalize(query)))


def query_terms(query, aliases=(), limit=6):
    """Expand known course terms for lexical search; preserve the semantic query."""
    original = retrieval_topic(query)
    matches = []
    for group in aliases:
        normalized = tuple(dict.fromkeys(normalize(term) for term in group))
        for term in normalized:
            matches.extend((m.start(), m.end(), normalized) for m in re.finditer(term_pattern(term), original))
    matches.sort(key=lambda item: item[1] - item[0], reverse=True)
    covered, groups = [], []
    for start, end, group in matches:
        if any(start < right and end > left for left, right in covered):
            continue
        covered.append((start, end))
        if group not in groups:
            groups.append(group)
    parts = re.split(r"[，,、；;：:！？!?\n]|\s+(?:and|or|versus|vs|和|与|及)\s+", original)
    ignored = {"它", "我", "你", "他", "这", "那", "这个", "那个", "为什么", "继续", "简单点", "核心概念", "这门课的核心概念"}
    terms = [part.strip() for part in parts if part.strip() not in ignored and 1 <= len(part.strip()) <= 70] if not groups else []
    numbers = list(dict.fromkeys(re.findall(r"(?<![a-z0-9])\d+(?![a-z0-9])", original)))
    # Multiple literal keys locate the user's worked example, without translating it.
    if len(numbers) >= 2:
        groups = [(number,) for number in numbers[:3]] + groups
    groups.extend((term,) for term in dict.fromkeys(terms) if (term,) not in groups)
    return (groups if limit is None else groups[:limit]), query.strip()


def lexical_score(text, groups):
    if not groups:
        return 0.0
    text = normalize(text)
    matched = sum(any(re.search(term_pattern(term), text) for term in group) for group in groups)
    coverage = matched / len(groups)
    if coverage < 0.5:
        return 0.0
    # A full definition should outrank a repeated title or a diagram label.
    informative = min(1.0, len(text) / 100)
    definition = 0.05 if ":" in text or "定义" in text else 0.0
    return coverage * (0.75 + 0.20 * informative + definition)
