import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("NARRATOR_DATA", ROOT / "data"))
LLM_URL = os.environ.get("LLM_URL", "http://localhost:11435")
HOST = os.environ.get("NARRATOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("NARRATOR_PORT", "8082"))
MAX_STEPS = int(os.environ.get("NARRATOR_MAX_STEPS", "4"))
STEP_TOKENS = int(os.environ.get("NARRATOR_STEP_TOKENS", "350"))
SPEAK_TOKENS = int(os.environ.get("NARRATOR_SPEAK_TOKENS", "120"))
CYR_MIN = float(os.environ.get("NARRATOR_CYR_MIN", "0.7"))
TEMPERATURE = float(os.environ.get("NARRATOR_TEMPERATURE", "0.8"))
