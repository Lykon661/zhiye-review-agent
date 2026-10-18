"""Cache placement is explicit even after huggingface_hub was imported."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import numpy as np

from app.config import DEFAULT_EMBEDDING_MODEL, ROOT, Settings
from app.retrieval import LocalEmbedder, ONNX_GRANITE_REVISION, ONNX_MODEL_FILE, ONNX_MODEL_FILES


@pytest.mark.parametrize("home", [None, "./.cache/test-hf-home", "absolute"], ids=["default", "relative", "absolute"])
def test_embedding_cache_is_explicit_and_resolved_from_project_root(home, tmp_path, monkeypatch):
    download_calls = []
    snapshot = tmp_path / "downloaded-snapshot"

    def download_model(**kwargs):
        download_calls.append(kwargs)
        return str(snapshot)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download_model))
    if home is None:
        monkeypatch.delenv("HF_HOME", raising=False)
        expected_home = ROOT / ".cache" / "huggingface"
    elif home == "absolute":
        expected_home = tmp_path / "explicit-hf-home"
        monkeypatch.setenv("HF_HOME", str(expected_home))
    else:
        monkeypatch.setenv("HF_HOME", home)
        expected_home = ROOT / home

    # The project cache may already hold the real snapshot; this checks where downloads go.
    monkeypatch.setattr(LocalEmbedder, "_cached_snapshot", lambda self, cache_folder: None)
    # Model configuration must not depend on the shell's current directory.
    monkeypatch.chdir(tmp_path)
    embedder = LocalEmbedder(Settings(embedding_local_files_only=True))
    assert embedder.prepare_model() == snapshot
    assert embedder.ready is False
    assert len(download_calls) == 1
    kwargs = download_calls[0]
    assert kwargs["repo_id"] == DEFAULT_EMBEDDING_MODEL
    assert kwargs["revision"] == ONNX_GRANITE_REVISION
    assert Path(kwargs["cache_dir"]) == expected_home.resolve() / "hub"
    assert kwargs["local_files_only"] is True
    assert kwargs["allow_patterns"] == ONNX_MODEL_FILES


def test_onnx_embedding_uses_cls_normalizes_and_batches_queries():
    calls = []
    tokenizer_calls = []

    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name=name, type="tensor(int64)") for name in (
                "input_ids", "attention_mask",
            )]

        def run(self, outputs, inputs):
            calls.append(inputs)
            assert outputs == ["last_hidden_state"]
            size = inputs["input_ids"].shape[0]
            hidden = np.full((size, 2, 768), 100, dtype=np.float32)
            hidden[:, 0, :] = 0
            hidden[:, 0, :2] = [3, 4]
            return [hidden]

    class BatchTokenizer:
        def encode_batch(self, texts):
            tokenizer_calls.append(texts)
            return [SimpleNamespace(ids=[2, 10], attention_mask=[1, 1], type_ids=[0, 0]) for _ in texts]

    embedder = LocalEmbedder(Settings())
    embedder._model = Session()
    embedder._batch_tokenizer = BatchTokenizer()
    texts = ["Query 中文 " + str(index) for index in range(17)]
    vectors = embedder.embed(texts, query=True)
    assert [call["input_ids"].shape[0] for call in calls] == [8, 8, 1]
    assert all(call["input_ids"].dtype == np.int64 for call in calls)
    assert tokenizer_calls[0][0] == "Query 中文 0"
    assert np.asarray(vectors).shape == (17, 768)
    assert np.allclose(np.asarray(vectors)[:, :2], [0.6, 0.8])
    assert np.allclose(np.asarray(vectors)[:, 2:], 0)
    assert embedder.embed([]) == []


def test_complete_onnx_snapshot_does_not_contact_hub(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    snapshot = tmp_path / "hub" / ("models--" + DEFAULT_EMBEDDING_MODEL.replace("/", "--")) / "snapshots" / ONNX_GRANITE_REVISION
    (snapshot / "onnx").mkdir(parents=True)
    for name in ("config.json", "tokenizer.json", "1_Pooling/config.json", "onnx/" + ONNX_MODEL_FILE):
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture")

    def unexpected_download(**kwargs):
        raise AssertionError("A complete local snapshot must not request the Hub")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=unexpected_download))
    embedder = LocalEmbedder(Settings())
    assert embedder.prepare_model() == snapshot
    assert embedder.ready is False
