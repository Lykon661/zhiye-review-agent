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
    ("二叉树", "binary tree", "binary trees"),
    ("二叉搜索树", "二叉查找树", "binary search tree", "binary search trees", "bst"),
    ("满二叉树", "full binary tree", "full binary trees"),
    ("完全二叉树", "complete binary tree", "complete binary trees"),
    ("完美二叉树", "perfect binary tree", "perfect binary trees"),
    ("AVL树", "AVL 树", "avl tree", "avl trees", "avl"),
    ("伸展树", "splay tree", "splay trees"),
    ("自调整树", "self adjusting tree", "self adjusting trees", "self-adjusting tree", "self-adjusting trees"),
    ("多路搜索树", "多路查找树", "multi-way search tree", "multi-way search trees", "multiway search tree", "multiway search trees", "multi-way tree", "multi-way trees"),
    ("B树", "B 树", "b-tree", "b-trees", "b tree", "b trees", "btree", "btrees"),
    ("B+树", "B+ 树", "b+tree", "b+trees", "b+ tree", "b+ trees", "b+-tree", "b+-trees"),
    ("2-3树", "2-3 树", "2-3 tree", "2-3 trees"),
    ("2-3-4树", "2-3-4 树", "2-3-4 tree", "2-3-4 trees"),
    ("树", "tree", "trees"),
    ("排序算法", "排序", "sorting", "sort", "sorts"),
    ("冒泡排序", "冒泡", "bubble sort", "bubblesort"),
    ("选择排序", "selection sort", "selectionsort"),
    ("插入排序", "insertion sort", "insertionsort"),
    ("希尔排序", "shell sort", "shellsort"),
    ("归并排序", "合并排序", "merge sort", "mergesort"),
    ("快速排序", "快排", "quick sort", "quicksort"),
    ("堆排序", "堆排", "heap sort", "heapsort"),
    ("桶排序", "bucket sort", "bucketsort"),
    ("计数排序", "counting sort", "countingsort"),
    ("基数排序", "radix sort", "radixsort"),
    ("外部排序", "外排序", "external sorting", "external sort"),
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


def retrieval_topic(query):
    """Remove the request wording, keeping the actual subject for retrieval."""
    topic = normalize(query).strip("*_` ")
    topic = re.sub(r"^(?:(?:请|帮我|带我|给我|我想|我要|想要)\s*)+", "", topic)
    topic = re.sub(r"^(?:(?:复习一下|复习|学习一下|学习|列举|对比一下|对比|比较一下|比较|总结一下|梳理一下|一下|各种|各类|各个|每种|讲解一下|讲解|解释一下|解释|简单说明|什么是|什么叫|what is |what are |explain |define )\s*)+", "", topic)
    return re.sub(r"(?:(?:的)?(?:相关知识|相关内容)|是什么|是什么意思|的定义|的概念|[?？。！!])+$", "", topic).strip()


def overview_request(query):
    """A comparison/overview needs coverage, rather than a few similar chunks."""
    return bool(re.search(r"列举|对比|比较|总结|梳理|汇总|总览|各种|各类|各个|每种|所有|全部|\b(?:compare|comparison|overview|summarize|all types|every)\b", normalize(query)))


def query_terms(query, notes=""):
    aliases = list(ALIASES)
    # Current review notes may provide bilingual names, but their definitions are
    # not promoted to evidence. Locate the original document using names only.
    for match in re.finditer(r"([\u4e00-\u9fff]{1,24})(?:\*\*)?\s*[（(]([A-Za-z][A-Za-z0-9 -]{2,69})", notes):
        aliases.append((match[1], normalize(match[2])))
    original = retrieval_topic(query)
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
        topic = original
        usable = 2 <= len(topic) <= 70 or bool(re.fullmatch(r"[\u4e00-\u9fff]", topic))
        if usable and topic not in {"它", "我", "你", "他", "这", "那", "这个", "那个", "为什么", "继续", "简单点", "核心概念", "这门课的核心概念"}:
            groups.append((topic,))
    english = list(dict.fromkeys(term for group in groups for term in group if re.search(r"[a-z]", term)))
    expanded = (original or query) + ("；检索术语：" + " / ".join(english[:6]) if english else "")
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
