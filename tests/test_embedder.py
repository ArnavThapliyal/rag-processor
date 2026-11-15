import os
import sys
import types

import numpy as np
import pytest

import pipeline.embedder as emb
from pipeline.embedder import Embedder, resolve_device
from stub_embedder import StubModel, text_vector


def test_rows_come_back_in_input_order_despite_length_sorting():
    texts = ["short", "a much longer piece of text " * 5, "mid length text", "x"]
    model = StubModel()
    out = Embedder(model=model, batch_size=2).encode(texts)
    assert out.shape == (4, 1024) and out.dtype == np.float32
    for row, text in zip(out, texts):
        assert np.allclose(row, text_vector(text), atol=1e-6)
    assert model.batch_sizes == [2, 2]  # batched, not one call per text


def test_vectors_are_unit_length_even_if_the_model_does_not_normalise():
    out = Embedder(model=StubModel(normalise=False), batch_size=8).encode(["a", "b", "c"])
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)


def test_out_of_memory_halves_the_batch_and_keeps_the_smaller_size():
    model = StubModel(oom_above=4)
    e = Embedder(model=model, batch_size=16)
    texts = [f"text {i}" for i in range(20)]
    out = e.encode(texts)
    assert np.allclose(out[7], text_vector("text 7"), atol=1e-6)
    assert max(model.batch_sizes) <= 4 and sum(model.batch_sizes) == 20 and e.batch_size == 4


def test_errors_that_are_not_memory_and_oom_at_batch_one_are_raised():
    class Broken:
        def encode(self, batch, **kw):
            raise RuntimeError("tokenizer exploded")

    with pytest.raises(RuntimeError, match="exploded"):
        Embedder(model=Broken()).encode(["a"])
    with pytest.raises(RuntimeError, match="out of memory"):
        Embedder(model=StubModel(oom_above=0), batch_size=4).encode(["a", "b"])


def test_bad_model_output_is_rejected():
    class WrongDim:
        def encode(self, batch, **kw):
            return np.ones((len(batch), 10), dtype=np.float32)

    class NaNs:
        def encode(self, batch, **kw):
            return np.full((len(batch), 1024), np.nan, dtype=np.float32)

    with pytest.raises(ValueError, match="shape"):
        Embedder(model=WrongDim()).encode(["a"])
    with pytest.raises(ValueError, match="non-finite"):
        Embedder(model=NaNs()).encode(["a"])


def test_empty_input():
    assert Embedder(model=StubModel()).encode([]).shape == (0, 1024)


@pytest.mark.parametrize("cuda, mps, expected", [(True, True, "cuda"), (False, True, "mps"), (False, False, "cpu")])
def test_device_order_is_cuda_then_mps_then_cpu(monkeypatch, cuda, mps, expected):
    fake = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: cuda),
        backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: mps)),
    )
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert resolve_device(None) == expected
    assert resolve_device("cpu") == "cpu"  # an explicit EMBED_DEVICE always wins


def test_importing_the_module_does_not_import_torch_or_the_model():
    import subprocess

    code = "import sys, pipeline.embedder; print('torch' in sys.modules, 'sentence_transformers' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=os.getcwd())
    assert result.stdout.split() == ["False", "False"], result.stderr


@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="loads the real bge-m3 model; set RUN_SLOW=1")
def test_real_model_dimension_and_norm():
    e = Embedder()
    out = e.encode(["Revenue grew 12% year on year.", "राजस्व में वृद्धि हुई।"])
    assert out.shape == (2, emb.EMBEDDING_DIM) and np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-3)
