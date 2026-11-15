"""Deterministic stand-ins for the bge-m3 model, so tests never download or load it."""

import hashlib

import numpy as np

from pipeline.embedder import Embedder

DIM = 1024


def text_vector(text: str) -> np.ndarray:
    """A fixed unit vector per text (seeded by its hash)."""
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
    v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


class StubModel:
    """Mimics SentenceTransformer.encode. `oom_above` simulates a device that only fits small batches."""

    def __init__(self, oom_above=None, normalise=True):
        self.oom_above = oom_above
        self.normalise = normalise
        self.batch_sizes = []

    def encode(self, batch, batch_size=None, normalize_embeddings=False, convert_to_numpy=True,
               show_progress_bar=False):
        if self.oom_above is not None and len(batch) > self.oom_above:
            raise RuntimeError("MPS backend out of memory")
        self.batch_sizes.append(len(batch))
        vectors = np.stack([text_vector(t) for t in batch])
        return vectors if self.normalise else vectors * 3.0


def make_stub_embedder() -> Embedder:
    """Picklable factory for the embedding process in multi-process tests."""
    return Embedder(model=StubModel(), batch_size=16)
