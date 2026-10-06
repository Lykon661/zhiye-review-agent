"""JSON-lines worker; run with formula-worker/.venv, never the Agent's NumPy."""
import contextlib
import json
import os
from pathlib import Path
import sys


def model_paths():
    import httpx

    directory = Path(os.environ["FORMULA_MODEL_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    for argument, filename in (("image_resizer_path", "image_resizer.onnx"), ("encoder_path", "encoder.onnx"),
                               ("decoder_path", "decoder.onnx"), ("tokenizer_json", "tokenizer.json")):
        target = directory / filename
        if not target.is_file() or not target.stat().st_size:
            url = "https://github.com/RapidAI/RapidLaTeXOCR/releases/download/v0.0.0/" + filename
            temporary = target.with_suffix(target.suffix + ".part")
            print(f"Downloading formula model: {filename}", file=sys.stderr, flush=True)
            try:
                with httpx.stream("GET", url, follow_redirects=True, timeout=90) as response:
                    response.raise_for_status()
                    size = 0
                    with temporary.open("wb") as output:
                        for block in response.iter_bytes():
                            size += len(block)
                            if size > 1024 * 1024 * 1024:
                                raise ValueError("Formula model download exceeded size limit")
                            output.write(block)
                    expected = response.headers.get("content-length")
                    if size == 0 or (expected and expected.isdecimal() and size != int(expected)):
                        raise ValueError("Incomplete formula model download")
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        result[argument] = target
    return result


def main():
    engine = None
    for line in sys.stdin:
        try:
            request = json.loads(line)
            # Model download/status output must never corrupt the protocol.
            with contextlib.redirect_stdout(sys.stderr):
                if engine is None:
                    from rapid_latex_ocr import LaTeXOCR
                    engine = LaTeXOCR(**model_paths())
                latex, _ = engine(request["path"])
            response = {"latex": latex}
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            response = {"error": "图片公式识别失败，请检查模型下载和公式识别日志。"}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
