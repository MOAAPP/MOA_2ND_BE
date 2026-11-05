import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATASET_PATH = Path(os.environ.get('MOA_DATASET_PATH', str(BACKEND_DIR / 'app/data/source/moa_20users_financial_test_JSON.zip'))).resolve()
CORS_ORIGINS = [s.strip() for s in os.getenv('MOA_CORS_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(',') if s.strip()]
DEFAULT_DEMO_USER_ID = os.getenv('MOA_DEFAULT_DEMO_USER', 'S01')
