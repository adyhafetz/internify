import os
from dotenv import load_dotenv

load_dotenv()

# Which AI service answers: "openai" (default) or "gemini" (Google's free tier, through its OpenAI-compatible endpoint).
# OPENAI_API_KEY below is simply "the key of the chosen service", whichever it is.
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()
if AI_PROVIDER == "gemini":
    OPENAI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    AI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
else:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    AI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip() or None

# A small model for reading, a mid-size one for writing and checking.
EXTRACTOR_MODEL = os.getenv("EXTRACTOR_MODEL", "gpt-5.4-nano")
CRITIC_MODEL = os.getenv("CRITIC_MODEL", "gpt-5.4-mini")
GENERATOR_MODEL = os.getenv("GENERATOR_MODEL", "gpt-5.4-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
# Reading a whole resume is a one-off job where accuracy matters more than cost, so use the stronger model.
IMPORT_MODEL = os.getenv("IMPORT_MODEL", GENERATOR_MODEL)
# The small AI helpers (checking a page is a real job posting, reading career pages, explaining a match) use the extractor model.
SCOUT_MODEL = os.getenv("SCOUT_MODEL", EXTRACTOR_MODEL)
DISCOVERER_MODEL = os.getenv("DISCOVERER_MODEL", EXTRACTOR_MODEL)
MATCHER_MODEL = os.getenv("MATCHER_MODEL", EXTRACTOR_MODEL)

SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "85"))
MAX_GENERATOR_ITERATIONS = int(os.getenv("MAX_GENERATOR_ITERATIONS", "3"))
MAX_TEX_FIX_RETRIES = int(os.getenv("MAX_TEX_FIX_RETRIES", "2"))
TECTONIC_TIMEOUT_SECONDS = int(os.getenv("TECTONIC_TIMEOUT_SECONDS", "600"))
TOP_K_FACTS = int(os.getenv("TOP_K_FACTS", "10"))

FACT_STORE_PATH = os.getenv("FACT_STORE_PATH", "output/data/facts.json")
SKELETON_PATH = os.getenv("SKELETON_PATH", "output/data/resume_skeleton.json")
EMBEDDING_CACHE_PATH = os.getenv("EMBEDDING_CACHE_PATH", "output/data/embeddings_cache.json")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
TEX_TEMPLATE_PATH = os.getenv("TEX_TEMPLATE_PATH", "service/latex/jake_template.tex.jinja")
MAX_BULLETS_PER_ENTRY = int(os.getenv("MAX_BULLETS_PER_ENTRY", "4"))

# The Archivist may ask the user about job requirements that are missing from their saved experience.
# Your GitHub username: before asking you about a tool, the Archivist looks for it in your public repos.
GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "").strip().lstrip("@")
MAX_SKILL_QUESTIONS = int(os.getenv("MAX_SKILL_QUESTIONS", "2"))
