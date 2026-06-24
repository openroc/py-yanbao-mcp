import logging
from typing import TYPE_CHECKING

import numpy as np

from .config import EMBEDDING_MODEL, EMBEDDING_DIM

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


logger = logging.getLogger(__name__)

_model = None
_model_available = True


def _load_model():
    global _model, _model_available
    if _model is not None:
        return _model
    if not _model_available:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _model = SentenceTransformer(
            EMBEDDING_MODEL,
            trust_remote_code=True,
            local_files_only=True,
        )
        logger.info("Model loaded (from cache)")
        return _model
    except OSError:
        logger.info("Model not in cache, downloading...")
    except Exception as e:
        logger.warning(f"Embedding model failed to load: {e}. Vector search disabled.")
        _model_available = False
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL, trust_remote_code=True)
        logger.info("Model loaded")
        return _model
    except Exception as e:
        logger.warning(f"Embedding model failed to load: {e}. Vector search disabled.")
        _model_available = False
        return None


def is_available() -> bool:
    return _load_model() is not None


def encode(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    model = _load_model()
    if model is None:
        return np.empty((0, EMBEDDING_DIM), dtype=np.float32)
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.astype(np.float32)


def encode_one(text: str) -> list[float]:
    vec = encode([text])[0]
    return vec.tolist()
