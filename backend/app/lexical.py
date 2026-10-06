"""Term matching supplements embeddings; aliases translate queries, never supply facts."""
import re
import unicodedata

# Common vocabulary only. Every answer still needs text from this course.
ALIASES = (
    ("无监督学习", "非监督学习", "unsupervised learning", "unsupervised"),
    ("半监督学习", "semi-supervised learning", "semi supervised learning"),
    ("监督学习", "supervised learning", "supervised"),
    ("机器学习", "machine learning"), ("强化学习", "reinforcement learning"),
    ("线性回归", "linear regression"), ("逻辑回归", "logistic regression"),
    ("梯度下降", "gradient descent"), ("随机梯度下降", "stochastic gradient descent", "sgd"),
    ("支持向量机", "support vector machine", "svm"),
    ("过拟合", "overfitting", "over-fitting"), ("欠拟合", "underfitting", "under-fitting"),
    ("正则化", "regularization"), ("聚类", "clustering"),
    ("多类分类", "multi-class classification", "multiclass classification"),
    ("朴素贝叶斯", "naive bayes"), ("决策树", "decision tree"),
    ("独立同分布", "iid", "independent and identically distributed"),
)


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def term_pattern(term):
    term = normalize(term)
    # English "supervised" must not match "unsupervised"; preserve Chinese phrases.
    left = r"(?<![a-z0-9])" if term and term[0].isascii() and term[0].isalnum() else ""
    right = r"(?![a-z0-9])" if term and term[-1].isascii() and term[-1].isalnum() else ""
    return left + re.escape(term) + right


def query_terms(query, notes=""):
    aliases = list(ALIASES)
    # Current review notes may provide bilingual names, but their definitions are
    # not promoted to evidence. Locate the original document using names only.
    for match in re.finditer(r"([\u4e00-\u9fff]{2,24})(?:\*\*)?\s*[（(]([A-Za-z][A-Za-z0-9 -]{2,69})", notes):
        aliases.append((match[1], normalize(match[2])))
    original = normalize(query)
    matches = []
    for group in aliases:
        for term in group:
            for match in re.finditer(term_pattern(term), original):
                matches.append((match.start(), match.end(), group))
    matches.sort(key=lambda item: item[1] - item[0], reverse=True)
    covered, groups = [], []
    for start, end, group in matches:
        if any(start < other_end and end > other_start for other_start, other_end in covered):
            continue
        covered.append((start, end))
        normalized_group = tuple(dict.fromkeys(normalize(term) for term in group))
        if normalized_group not in groups:
            groups.append(normalized_group)
    if not groups:
        topic = re.sub(r"^(?:请|帮我|解释一下|解释|简单说明|什么是|什么叫|what is |what are |explain |define )+", "", original)
        topic = re.sub(r"(?:是什么|是什么意思|的定义|的概念|[?？。])+$", "", topic).strip()
        if 2 <= len(topic) <= 70 and topic not in {"它", "这个", "那个", "为什么", "继续", "简单点", "核心概念", "这门课的核心概念"}:
            groups.append((topic,))
    english = list(dict.fromkeys(term for group in groups for term in group if re.search(r"[a-z]", term)))
    expanded = query + ("；检索术语：" + " / ".join(english[:6]) if english else "")
    return groups[:6], expanded


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
