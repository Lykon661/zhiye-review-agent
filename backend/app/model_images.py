"""Attach immutable local question figures as pi-ai image content blocks."""
import base64
import json

from .question_images import IMAGE_URL

IMAGE_INPUT_POLICY = "question_images_v1"
MAX_IMAGES = 16
MAX_IMAGE_BYTES = 16 * 1024 * 1024


def question_image_urls(value):
    urls = {}

    def visit(item):
        if isinstance(item, str):
            for match in IMAGE_URL.finditer(item):
                urls.setdefault(match[1], None)
        elif isinstance(item, dict):
            for key, child in item.items():
                # Old practice snapshots are conversational history, not the
                # current question. Never attach their figures to a new turn.
                if key != "history":
                    visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return list(urls)


def image_resolution_current(question, resolution):
    """Old text-only model answers cannot be reused for a question with figures."""
    if not resolution:
        return False
    return not (resolution.get("origin") == "model"
                and question_image_urls([question.section, question.stem, question.options])
                and resolution.get("image_policy") != IMAGE_INPUT_POLICY)


def attach_question_images(messages, data_dir):
    """Keep the text payload, followed by labeled PNG attachments; never fetch URLs."""
    target = next((i for i in range(len(messages) - 1, -1, -1)
                   if messages[i].get("role") == "user"), None)
    if target is None or not isinstance(messages[target].get("content"), str):
        return messages
    text = messages[target]["content"]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = text
    urls = question_image_urls(payload)
    if not urls:
        return messages
    if len(urls) > MAX_IMAGES:
        raise ValueError(f"本次题图超过 {MAX_IMAGES} 张，请拆分题目文件或缩小问题范围后重试。")
    directory = (data_dir / "question-images").resolve()
    content = [{"type": "text", "text": text}]
    total = 0
    for url in urls:
        path = (directory / url.rsplit("/", 1)[1]).resolve()
        if path.parent != directory or not path.is_file():
            raise ValueError("题目原图不存在，请重新解析题目文件并重新整理题目后再试。")
        total += path.stat().st_size
        if total > MAX_IMAGE_BYTES:
            raise ValueError("本次题图总大小超过 16 MB，请拆分题目或降低图片大小后重试。")
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("题目原图已损坏，请重新解析题目文件后再试。")
        content.extend([{"type": "text", "text": f"以下图片对应原文标记：{url}"},
                        {"type": "image", "data": base64.b64encode(data).decode("ascii"),
                         "mimeType": "image/png"}])
    result = [dict(message) for message in messages]
    result[target]["content"] = content
    return result
