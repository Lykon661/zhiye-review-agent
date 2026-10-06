"""Lazy, local OCR with cached recognition and an isolated formula worker."""
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
import json
import logging
import os
from pathlib import Path
from queue import Queue
import re
import subprocess
from threading import RLock, Thread

from .config import ROOT

logger = logging.getLogger(__name__)
CACHE_VERSION = "rapidocr-3.9.2-latex-0.0.9-v1"


@dataclass
class VisualResult:
    lines: list[dict] = field(default_factory=list)
    formulas: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: bool = False


class FormulaRecognitionRejected(ValueError):
    """An unusable prediction for one crop, not a formula worker failure."""


def _is_decorative_image(image):
    # Conservative pixel checks before loading or calling either OCR model.
    # Do not use a wide aspect ratio alone: formula screenshots are often wide.
    width, height = image.size
    if min(width, height) <= 4 or (width <= 48 and height <= 48):
        return True
    # Check each channel, so a coloured diagram is not lost in grayscale.
    return all(high - low <= 8 for low, high in image.getextrema())


def normalized(text):
    return re.sub(r"\s+", "", text).casefold()


def formula_like(text):
    # Avoid sending prose, logos and photographs to a formula-only recognizer.
    if len(text) > 220 or len(re.findall(r"[\u4e00-\u9fff]", text)) > 6:
        return False
    return bool(re.search(r"[∑∫∂∇√∞≤≥≠≈]|[A-Za-zα-ωΑ-Ω][\w(){} ,.^]*\s*[=±]|[A-Za-z]\s*[_^]\s*[\w{]|\d\s*[+*/=]\s*\d", text))


class VisualRecognizer:
    def __init__(self, settings):
        self.settings = settings
        if not 0 <= settings.ocr_min_confidence <= 1 or settings.ocr_max_image_pixels < 100000:
            raise ValueError("OCR 置信度应在 0–1 之间，图片像素上限应至少为 100000。")
        self.cache_dir = settings.data_dir / "ocr-cache"
        self.lock = RLock()
        self.ocr = None
        self.process = None
        self.responses = None
        self.log_stream = None
        self.ocr_error = None
        self.formula_error = None

    def begin_document(self):
        # Failed downloads are retried on the next document/reparse, not every picture.
        self.ocr_error = self.formula_error = None

    def close(self):
        with self.lock:
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
                for stream in (self.process.stdin, self.process.stdout):
                    if stream:
                        stream.close()
                self.process = None
            if self.log_stream:
                self.log_stream.close()
                self.log_stream = None

    def _formula(self, image):
        if self.formula_error:
            raise ValueError(self.formula_error)
        try:
            if self.process is None or self.process.poll() is not None:
                self.close()
                executable = ROOT / "formula-worker" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                if not executable.is_file():
                    raise ValueError("公式识别环境未安装，请运行 scripts/setup.ps1。")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                self.log_stream = (self.cache_dir / "formula-worker.log").open("a", encoding="utf-8")
                self.process = subprocess.Popen(
                    [str(executable), "-u", str(Path(__file__).with_name("formula_worker.py"))],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log_stream,
                    text=True, encoding="utf-8", cwd=str(ROOT),
                    env={**os.environ, "PYTHONUTF8": "1", "FORMULA_MODEL_DIR": str((self.settings.data_dir / "ocr-models" / "formula").resolve())},
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                queue = Queue()
                self.responses = queue
                process = self.process

                def read_responses():
                    for line in process.stdout:
                        queue.put(line)
                    queue.put(None)

                Thread(target=read_responses, daemon=True).start()
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            temporary = self.cache_dir / ("formula-" + sha256(buffer.getvalue()).hexdigest() + ".png")
            temporary.write_bytes(buffer.getvalue())
            try:
                self.process.stdin.write(json.dumps({"path": str(temporary.resolve())}) + "\n")
                self.process.stdin.flush()
                response = self.responses.get(timeout=600)
                if response is None:
                    raise ValueError("公式识别进程提前结束，请检查公式识别日志。")
                data = json.loads(response)
                if data.get("error"):
                    raise ValueError(data["error"])
                latex = str(data.get("latex", "")).strip()
                if not latex or len(latex) > 4000 or latex.count("{") != latex.count("}"):
                    raise FormulaRecognitionRejected("图片公式输出为空或结构异常，未采用该结果。")
                return latex
            finally:
                temporary.unlink(missing_ok=True)
        except FormulaRecognitionRejected:
            # A decorative/non-mathematical crop must not disable the worker
            # for all subsequent, potentially useful formulas in this document.
            raise
        except Exception as exc:
            self.close()
            self.formula_error = str(exc) if isinstance(exc, ValueError) else "图片公式识别超时或失败，请检查模型下载和公式识别日志。"
            logger.exception("Formula recognition failed")
            raise ValueError(self.formula_error) from exc

    def recognize(self, image, context="", existing_text=""):
        from PIL import Image, ImageOps

        if not self.settings.ocr_enabled:
            return VisualResult(warnings=["图片 OCR 已在配置中关闭。"])
        with self.lock:
            try:
                if isinstance(image, bytes):
                    image = Image.open(BytesIO(image))
                image = ImageOps.exif_transpose(image)
                if image.width * image.height > self.settings.ocr_max_image_pixels:
                    ratio = (self.settings.ocr_max_image_pixels / (image.width * image.height)) ** 0.5
                    image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
                if "A" in image.getbands():
                    rgba = image.convert("RGBA")
                    background = Image.new("RGBA", image.size, "white")
                    background.alpha_composite(rgba)
                    image = background.convert("RGB")
                else:
                    image = image.convert("RGB")
                if _is_decorative_image(image):
                    return VisualResult(skipped=True)
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                # Context affects formula selection; existing text only affects deduplication.
                cache_key = sha256(buffer.getvalue() + f"{CACHE_VERSION}:{self.settings.ocr_min_confidence}:{self.settings.formula_ocr_enabled}:{context}".encode()).hexdigest()
                cache_file = self.cache_dir / (cache_key + ".json")
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    result = VisualResult(**data)
                except (OSError, ValueError, TypeError):
                    result = self._recognize(image, buffer.getvalue())
                    # Pure formula image: no prose detected and a strong mathematical context.
                    if not result.lines and not result.formulas and not result.warnings and self.settings.formula_ocr_enabled:
                        math_context = re.search(r"公式|方程|梯度|回归|[Ff]ormula|[Ee]quation|[Gg]radient|[Rr]egression", context)
                        if math_context and 1.5 < image.width / image.height < 15 and image.height <= 600:
                            try:
                                result.formulas.append({"latex": self._formula(image), "bbox": [0, 0, image.width, image.height]})
                            except ValueError as exc:
                                result.warnings.append(str(exc))
                    if not result.warnings:
                        try:
                            self.cache_dir.mkdir(parents=True, exist_ok=True)
                            cache_file.write_text(json.dumps(result.__dict__, ensure_ascii=False), encoding="utf-8")
                        except OSError:
                            logger.warning("Could not cache OCR output", exc_info=True)
                original = normalized(existing_text)
                # Retain recognized source positions and scores in SQLite, omit duplicate text.
                return VisualResult(
                    lines=[line for line in result.lines if not original or normalized(line["text"]) not in original],
                    formulas=result.formulas, warnings=result.warnings,
                )
            except Exception:
                logger.exception("Image decoding/OCR failed")
                return VisualResult(warnings=["图片读取或 OCR 失败，未纳入该图片内容；请查看后端日志。"])

    def _recognize(self, image, image_bytes):
        result = VisualResult()
        if self.ocr_error:
            result.warnings.append(self.ocr_error)
            return result
        try:
            if self.ocr is None:
                from rapidocr import RapidOCR
                self.ocr = RapidOCR(params={"Global.text_score": 0.3})
            output = self.ocr(image_bytes)
        except Exception:
            self.ocr_error = "文字 OCR 模型加载或识别失败，请检查模型下载和后端日志后重新解析。"
            logger.exception("RapidOCR failed")
            result.warnings.append(self.ocr_error)
            return result
        if output.txts is None or output.boxes is None or output.scores is None:
            return result
        low_confidence = 0
        rows = []
        for box, text, score in zip(output.boxes, output.txts, output.scores):
            xs, ys = [float(point[0]) for point in box], [float(point[1]) for point in box]
            bounds = [max(0, int(min(xs))), max(0, int(min(ys))), min(image.width, int(max(xs)) + 1), min(image.height, int(max(ys)) + 1)]
            text = str(text).strip()
            if not text:
                continue
            rows.append({"text": text, "score": float(score), "bbox": bounds})
            if float(score) >= self.settings.ocr_min_confidence:
                result.lines.append(rows[-1])
            else:
                low_confidence += 1
        if low_confidence:
            result.warnings.append(f"{low_confidence} 处低置信度图片文字未纳入知识库。")
        if not self.settings.formula_ocr_enabled:
            return result
        # Group horizontally adjacent detections on one row before reading a formula.
        groups = []
        for row in sorted(rows, key=lambda item: (item["bbox"][1], item["bbox"][0])):
            left, top, right, bottom = row["bbox"]
            center, height = (top + bottom) / 2, bottom - top
            group = next((item for item in reversed(groups) if abs(item["center"] - center) <= max(height, item["height"]) * 0.4 and left - item["bbox"][2] < height * 6), None)
            if group is None:
                groups.append({"text": row["text"], "bbox": row["bbox"].copy(), "center": center, "height": height})
            else:
                group["text"] += " " + row["text"]
                group["bbox"] = [min(left, group["bbox"][0]), min(top, group["bbox"][1]), max(right, group["bbox"][2]), max(bottom, group["bbox"][3])]
        for group in groups:
            if not formula_like(group["text"]):
                continue
            left, top, right, bottom = group["bbox"]
            padding = max(4, int((bottom - top) * 0.25))
            bounds = [max(0, left - padding), max(0, top - padding), min(image.width, right + padding), min(image.height, bottom + padding)]
            try:
                with image.crop(tuple(bounds)) as cropped:
                    latex = self._formula(cropped)
                result.formulas.append({"latex": latex, "bbox": bounds, "ocr_text": group["text"]})
            except FormulaRecognitionRejected as exc:
                result.warnings.append(str(exc))
                continue
            except ValueError as exc:
                result.warnings.append(str(exc))
                break
        return result
