"""On-demand Apache Tika CLI extraction; no HTTP service or Office application."""

from contextlib import contextmanager
import logging
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from .config import ROOT

logger = logging.getLogger(__name__)
TIKA_VERSION = "3.3.2"


class TikaError(ValueError):
    """An actionable runtime or document error safe to show in upload status."""


def _runtime() -> tuple[str, Path]:
    jar_value = os.getenv("TIKA_APP_JAR", "").strip()
    jar = Path(jar_value) if jar_value else ROOT / ".runtime" / "tika" / f"tika-app-{TIKA_VERSION}.jar"
    if not jar.is_absolute():
        jar = ROOT / jar
    if not jar.is_file():
        raise TikaError("Tika 解析组件尚未安装。请运行 scripts/setup-tika.ps1 后重试。")
    java_value = os.getenv("TIKA_JAVA", "").strip()
    java = shutil.which(java_value or "java")
    if java_value and not java and Path(java_value).is_file():
        java = str(Path(java_value).resolve())
    if not java and not java_value and os.getenv("JAVA_HOME"):
        candidate = Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            java = str(candidate)
    if not java:
        raise TikaError("未找到 Java。Tika 3.3.2 需要 Java 11 或更新版本，可在 .env 设置 TIKA_JAVA 为 java.exe 路径。")
    return java, jar.resolve()


def _run(java: str, jar: Path, args: list[str], workspace: Path, output: Path, *, source: Path | None = None):
    log = workspace / "tika.log"
    try:
        # stdout goes to disk so large extracted files do not fill Python's memory.
        with output.open("wb") as stdout, log.open("wb") as stderr:
            entry = ["-jar", str(jar)]
            if source is not None:
                # Releases ship the helper precompiled for a compiler-free Java runtime.
                classes = jar.parent / "classes"
                entry = (["-cp", f"{jar}{os.pathsep}{classes}", source.stem]
                         if (classes / f"{source.stem}.class").is_file() else ["-cp", str(jar), str(source)])
            subprocess.run(
                [java, "-Xmx512m", "-Dfile.encoding=UTF-8", "-Djava.awt.headless=true",
                 *entry, *args],
                cwd=workspace, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                timeout=90, check=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
    except subprocess.TimeoutExpired as exc:
        raise TikaError("旧格式资料解析超时，请拆分文件后重试。") from exc
    except subprocess.CalledProcessError as exc:
        with log.open("rb") as stream:
            detail = stream.read(16000).decode("utf-8", errors="replace")
        logger.debug("Tika failed: %s", detail)
        if "UnsupportedClassVersionError" in detail:
            raise TikaError("Java 版本过低。请使用 Java 11 或更新版本，并重启后端。") from exc
        if "EncryptedDocumentException" in detail or "Password" in detail:
            raise TikaError("资料已加密，无法读取。请解除密码后重新上传。") from exc
        if "OutOfMemoryError" in detail:
            raise TikaError("资料超出解析内存上限，请拆分文件后重试。") from exc
        raise TikaError("Tika 未能读取资料，文件可能损坏或格式与扩展名不符。请重新导出后上传。") from exc
    except OSError as exc:
        raise TikaError("无法启动 Java/Tika，请检查 Java 路径和 Tika 安装。") from exc


@contextmanager
def extracted_document(path: Path, include_images: bool = True):
    java, jar = _runtime()
    with TemporaryDirectory(prefix="review-tika-") as directory:
        workspace = Path(directory)
        assets = workspace / "images"
        assets.mkdir()
        config = workspace / "tika-config.xml"
        # All image recognition uses this project's existing OCR, not an external Tesseract process.
        config.write_text(
            '<properties><parsers><parser class="org.apache.tika.parser.DefaultParser">'
            '<parser-exclude class="org.apache.tika.parser.ocr.TesseractOCRParser"/>'
            '</parser></parsers></properties>', encoding="utf-8",
        )
        xml = workspace / "content.xhtml"
        common = [f"--config={config}", "--encoding=UTF-8"]
        _run(java, jar, [*common, "--xml", str(path.resolve())], workspace, xml)
        if not xml.stat().st_size or xml.stat().st_size > 32 * 1024 * 1024:
            raise TikaError("资料提取结果为空或过大，请拆分文件后重试。")
        if path.suffix.lower() == ".doc":
            # Tika 3.3.2 emits bold/italic/underline but omits DOC run scripts.
            # Read these properties directly rather than inferring powers from text.
            try:
                _run(java, jar, [str(path.resolve())], workspace, workspace / "word-scripts.json",
                     source=Path(__file__).with_name("LegacyWordScripts.java"))
            except TikaError as exc:
                raise TikaError("DOC 字符格式读取失败。请使用完整 JDK 11 或更新版本，或将文件另存为 DOCX 后上传。") from exc
        if include_images:
            try:
                # The CLI writes progress to stdout in extract mode: keep it separate from XHTML.
                _run(java, jar, [*common, "--extract", f"--extract-dir={assets}", str(path.resolve())],
                     workspace, workspace / "attachments.log")
            except TikaError:
                # A failed embedded object must not discard already extracted document text.
                logger.debug("Tika embedded resource extraction incomplete", exc_info=True)
        yield xml, assets
