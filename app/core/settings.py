import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Load persistent local secrets from the backend root .env on every launch.
# Exported shell variables win, and real .env files stay outside Git.
from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / '.env', override=False)

# Vercel's FastAPI preset may load app.main directly instead of api/index.py.
# All runtime state must be under /tmp on a read-only serverless filesystem.
# These files are ephemeral and never constitute persistent financial storage.
if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
    os.environ.setdefault("MOA_RUNTIME_DIR", "/tmp/moa-finance/runtime")
    os.environ.setdefault("MOA_STAGE3_STORAGE_DIR", "/tmp/moa-finance/runtime/scenarios")
    os.environ.setdefault("MOA_STAGE6_STORAGE_DIR", "/tmp/moa-finance/runtime/stage6")

DATASET_PATH = Path(os.environ.get('MOA_DATASET_PATH', str(BACKEND_DIR / 'app/data/source/moa_20users_financial_test_JSON.zip'))).resolve()
CORS_ORIGINS = [s.strip() for s in os.getenv('MOA_CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(',') if s.strip()]
DEFAULT_DEMO_USER_ID = os.getenv('MOA_DEFAULT_DEMO_USER', 'S01')
