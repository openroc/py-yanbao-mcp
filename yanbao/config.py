import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIR / ".env"


def _load_env():
    env = {}
    env_path = ENV_FILE if ENV_FILE.exists() else None
    if env_path:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                env[k] = v
                os.environ.setdefault(k, v)
    return env


_env = _load_env()


def _get(key: str, default: str) -> str:
    return os.environ.get(key) or _env.get(key) or default


DATA_DIR = Path(_get("YANBAO_DATA_DIR", "H:/tmp/reports"))
DB_PATH = Path(_get("YANBAO_DB_PATH", str(PROJECT_DIR / "data" / "reports.db")))
EMBEDDING_MODEL = _get("YANBAO_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
CHUNK_SIZE = int(_get("YANBAO_CHUNK_SIZE", "512"))
CHUNK_OVERLAP = int(_get("YANBAO_CHUNK_OVERLAP", "64"))
INDEX_STATE_PATH = Path(
    _get("YANBAO_INDEX_STATE", str(PROJECT_DIR / "data" / ".index_state.json"))
)
LOG_LEVEL = _get("YANBAO_LOG_LEVEL", "INFO")
CORRUPTED_PDFS_PATH = Path(
    _get("YANBAO_CORRUPTED_PDFS", str(PROJECT_DIR / "data" / "corrupted_pdfs.jsonl"))
)
LLM_ENABLED = _get("YANBAO_LLM_ENABLED", "true").lower() == "true"
LLM_BASE_URL = _get("YANBAO_LLM_BASE_URL", "http://localhost:8080/v1")
LLM_MODEL = _get("YANBAO_LLM_MODEL", "")
LLM_TIMEOUT = int(_get("YANBAO_LLM_TIMEOUT", "60"))
LLM_API_KEY = _get("YANBAO_LLM_API_KEY", "")
EMBEDDING_DIM = 384
VEC_TABLE_NAME = "chunk_vectors"
FACTOR_VEC_TABLE_NAME = "factor_vectors"
FACTOR_TYPES = ["产能", "订单", "报价", "增速", "政策", "成本", "库存", "销量"]
