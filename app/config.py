"""Runtime settings. Every value can be overridden with an environment variable (see README)."""
import os
from pathlib import Path


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        print(f"WARNING: {name} is not an integer, using {default}")
        return int(default)


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        print(f"WARNING: {name} is not a number, using {default}")
        return float(default)


ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("NARRATOR_DATA", ROOT / "data"))

# --- server ---
HOST = os.environ.get("NARRATOR_HOST", "0.0.0.0")      # 127.0.0.1 = only this PC; 0.0.0.0 = also phone in LAN
PORT = _int("NARRATOR_PORT", 8082)

# --- llama-server / sampling ---
LLM_URL = os.environ.get("LLM_URL", "http://localhost:11435")
CHAT_TEMPLATE = os.environ.get("NARRATOR_TEMPLATE", "gemma4").strip().lower()   # app/prompt.py: gemma4 gemma3 chatml qwen3 llama3
TEMPERATURE = _float("NARRATOR_TEMPERATURE", 0.8)
TOP_P = _float("NARRATOR_TOP_P", 0.95)
MIN_P = _float("NARRATOR_MIN_P", 0.05)
REPEAT_PENALTY = _float("NARRATOR_REPEAT_PENALTY", 1.05)
SPEAK_TOKENS = _int("NARRATOR_SPEAK_TOKENS", 400)      # ceiling for one NPC line (anti-runaway, not a target)
CYR_MIN = _float("NARRATOR_CYR_MIN", 0.7)              # below this share of Cyrillic the step is regenerated
LENGTH_MULT = _float("NARRATOR_LENGTH_MULT", 1.0)      # default per-scene turn length multiplier
LULL_TURNS = _int("NARRATOR_LULL_TURNS", 5)            # quiet turns before a soft «the world may stir» reminder (0 = off)
LULL_COMBAT_TURNS = _int("NARRATOR_LULL_COMBAT_TURNS", 4)   # the same for a fight that drags on without change (0 = off)
INACTIVE_LEAVE = _int("NARRATOR_INACTIVE_LEAVE", 8)    # neutral NPCs auto-leave after N quiet turns (0 = off)

# --- party & experience ---
PARTY_MAX = _int("NARRATOR_PARTY_MAX", 4)              # party size including the hero

# --- memory / context ---
MEMORY = os.environ.get("NARRATOR_MEMORY", "1") != "0"
EMBED_DIR = Path(os.environ.get("NARRATOR_EMBED_DIR", ROOT / "models" / "multilingual-e5-small"))
CTX_BUDGET = _int("NARRATOR_CTX_BUDGET", 30000)        # must be <= llama-server --ctx-size
GEN_RESERVE = _int("NARRATOR_GEN_RESERVE", 3500)
RECENT_MAX = _int("NARRATOR_RECENT_MAX", 18000)        # tokens of unarchived history before background archive
ARCHIVE_BLOCK = _int("NARRATOR_ARCHIVE_BLOCK", 3000)
ARCHIVE_MIN = _int("NARRATOR_ARCHIVE_MIN", 1500)       # refuse undersized blocks
L1_MAX = _int("NARRATOR_L1_MAX", 8)
RETRIEVE_K = _int("NARRATOR_RETRIEVE_K", 4)

# --- saves ---
SNAPSHOTS = _int("NARRATOR_SNAPSHOTS", 60)             # rewind points kept per scene (0 = unlimited)
