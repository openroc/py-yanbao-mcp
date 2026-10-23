import logging
from typing import TYPE_CHECKING

from .config import EMBEDDING_MODEL, EMBEDDING_DIM

if TYPE_CHECKING:
    import numpy as np
    from sentence_transformers import SentenceTransformer


logger = logging.getLogger(__name__)

_model = None
_model_available = True


def _patch_ssl_cert_loading():
    """兼容部分 Windows 环境的默认证书库损坏。

    ssl.load_default_certs() 在这些机器上抛 SSLError，导致 aiohttp →
    datasets → sentence_transformers 整条导入链失败。降级为只加载
    SSL_CERT_FILE 指定的 CA 文件。
    """
    import ssl

    original = ssl.SSLContext.load_default_certs

    def load_default_certs(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except ssl.SSLError:
            logger.warning("默认证书库损坏，降级使用 SSL_CERT_FILE 指定的 CA")
            self.set_default_verify_paths()

    ssl.SSLContext.load_default_certs = load_default_certs


_patch_ssl_cert_loading()


def _empty_embeddings():
    """空向量占位。numpy 不可用时退化为空列表（调用方只检查长度）。"""
    try:
        import numpy as np
    except ImportError:
        return []
    return np.empty((0, EMBEDDING_DIM), dtype=np.float32)


def _load_model():
    global _model, _model_available
    if _model is not None:
        return _model
    if not _model_available:
        return None
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:
        logger.warning(f"Embedding 依赖不可用: {e}. 向量功能已禁用。")
        _model_available = False
        return None
    try:
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
        _model = SentenceTransformer(EMBEDDING_MODEL, trust_remote_code=True)
        logger.info("Model loaded")
        return _model
    except Exception as e:
        logger.warning(f"Embedding model failed to load: {e}. Vector search disabled.")
        _model_available = False
        return None


def is_available() -> bool:
    return _load_model() is not None


def encode(texts: list[str]) -> "np.ndarray":
    if not texts:
        return _empty_embeddings()
    model = _load_model()
    if model is None:
        return _empty_embeddings()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return embeddings.astype("float32")


def encode_one(text: str) -> list[float]:
    vec = encode([text])[0]
    return vec.tolist()
