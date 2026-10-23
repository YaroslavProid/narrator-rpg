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
CTX_BUDGET = int(os.environ.get("NARRATOR_CTX_BUDGET", "30000"))
GEN_RESERVE = int(os.environ.get("NARRATOR_GEN_RESERVE", "3500"))
RECENT_MAX = int(os.environ.get("NARRATOR_RECENT_MAX", "18000"))
ARCHIVE_BLOCK = int(os.environ.get("NARRATOR_ARCHIVE_BLOCK", "3000"))
ARCHIVE_MIN = int(os.environ.get("NARRATOR_ARCHIVE_MIN", "1500"))  # refuse undersized blocks
L1_MAX = int(os.environ.get("NARRATOR_L1_MAX", "8"))
RETRIEVE_K = int(os.environ.get("NARRATOR_RETRIEVE_K", "4"))
LENGTH_MULT = float(os.environ.get("NARRATOR_LENGTH_MULT", "1.0"))
EMBED_DIR = Path(os.environ.get("NARRATOR_EMBED_DIR", ROOT / "models" / "multilingual-e5-small"))
MEMORY = os.environ.get("NARRATOR_MEMORY", "1") != "0"
INACTIVE_LEAVE = int(os.environ.get("NARRATOR_INACTIVE_LEAVE", "5"))  # auto-<leave> after N quiet turns
