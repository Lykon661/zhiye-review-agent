from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EMBEDDING_MODEL = "ibm-granite/granite-embedding-311m-multilingual-r2"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: float = 90
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_device: str = "cpu"
    embedding_local_files_only: bool = False
    # No calibrated Granite cutoff yet; rank candidates and verify their evidence.
    retrieval_min_score: float = -1.0
    max_upload_bytes: int = 50 * 1024 * 1024
    ocr_enabled: bool = True
    formula_ocr_enabled: bool = True
    ocr_min_confidence: float = 0.65
    ocr_max_image_pixels: int = 12000000

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        data_path = Path(os.getenv("DATA_DIR", "data"))
        return cls(
            data_dir=(data_path if data_path.is_absolute() else ROOT / data_path),
            llm_base_url=os.getenv("LLM_BASE_URL", "").strip(),
            llm_model=os.getenv("LLM_MODEL", "").strip(),
            llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "90")),
            embedding_model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            embedding_device=os.getenv("EMBEDDING_DEVICE", "cpu"),
            embedding_local_files_only=os.getenv("EMBEDDING_LOCAL_FILES_ONLY", "false").lower() == "true",
            retrieval_min_score=float(os.getenv("RETRIEVAL_MIN_SCORE", "-1")),
            ocr_enabled=os.getenv("OCR_ENABLED", "true").lower() == "true",
            formula_ocr_enabled=os.getenv("FORMULA_OCR_ENABLED", "true").lower() == "true",
            ocr_min_confidence=float(os.getenv("OCR_MIN_CONFIDENCE", "0.65")),
            ocr_max_image_pixels=int(os.getenv("OCR_MAX_IMAGE_PIXELS", "12000000")),
        )
