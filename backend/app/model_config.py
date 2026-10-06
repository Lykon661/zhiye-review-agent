"""Local model preferences. Responses deliberately contain no credential values."""
from dataclasses import asdict, dataclass
import json
import os
from threading import RLock
from urllib.parse import urlparse

from .config import Settings

APIS = {"openai-completions", "openai-responses", "anthropic-messages", "google-generative-ai"}
PROVIDERS = {"custom", "openai", "anthropic", "google", "deepseek", "openrouter", "groq", "xai",
             "mistral", "moonshotai", "moonshotai-cn", "zai", "zai-coding-cn", "minimax",
             "minimax-cn", "together", "nvidia", "kimi-coding"}


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "custom"
    api: str = "openai-completions"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    allow_without_key: bool = False
    timeout_seconds: float = 90
    context_window: int = 32768
    max_tokens: int = 8192
    reasoning: bool = False
    supports_images: bool = False

    @property
    def configured(self):
        return bool(self.base_url and self.model and (self.api_key or self.allow_without_key))

    def public(self):
        values = asdict(self)
        values.pop("api_key")
        return {**values, "has_api_key": bool(self.api_key), "configured": self.configured}


class ModelConfigStore:
    def __init__(self, settings: Settings):
        self.path = settings.data_dir / "model-config.json"
        self.lock = RLock()
        self.value = ModelConfig(base_url=settings.llm_base_url, model=settings.llm_model,
                                 api_key=settings.llm_api_key, timeout_seconds=settings.llm_timeout_seconds)
        self.source = "environment" if self.value.configured else "none"
        if self.path.exists():
            # A saved UI configuration always wins, including explicit key removal.
            self.value = ModelConfig(**json.loads(self.path.read_text(encoding="utf-8")))
            self.source = "saved"

    def snapshot(self):
        with self.lock:
            return self.value

    def public(self):
        with self.lock:
            return {**self.value.public(), "source": self.source}

    def candidate(self, values: dict):
        with self.lock:
            values = dict(values)
            for field in ("provider", "api", "base_url", "model"):
                values[field] = values[field].strip()
            values["base_url"] = values["base_url"].rstrip("/")
            if values["provider"] not in PROVIDERS or values["api"] not in APIS:
                raise ValueError("请选择页面支持的服务商和接口协议")
            endpoint = urlparse(values["base_url"])
            if endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("接口地址必须是完整的 http(s) 地址，不能包含密钥、查询参数或片段")
            if endpoint.path.endswith(("/chat/completions", "/responses", "/messages")):
                raise ValueError("请填写接口根地址，例如 https://api.example.com/v1，不要包含 /chat/completions、/responses 或 /messages")
            if not values["model"]:
                raise ValueError("模型名称不能为空")
            if values["max_tokens"] > values["context_window"]:
                raise ValueError("最大输出 token 数不能超过上下文窗口")
            if values["allow_without_key"] and values["provider"] != "custom":
                raise ValueError("免密钥连接仅适用于自定义或本地模型服务")
            key = values.get("api_key")
            if key is None:
                if (values["provider"], values["api"], values["base_url"]) != (self.value.provider, self.value.api, self.value.base_url.rstrip("/")):
                    key = ""
                else:
                    key = self.value.api_key
            values["api_key"] = key.strip()
            candidate = ModelConfig(**values)
            if not candidate.configured:
                raise ValueError("请填写 API Key；本地免密钥服务可勾选“无需 API Key”")
            return candidate

    def save(self, values: dict):
        with self.lock:
            candidate = self.candidate(values)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as output:
                    os.chmod(temporary, 0o600)
                    json.dump(asdict(candidate), output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.value, self.source = candidate, "saved"
            return self.public()
