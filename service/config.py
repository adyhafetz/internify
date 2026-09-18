import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Cheap model for extraction and critique, stronger model for the actual rewrite.
EXTRACTOR_MODEL = os.getenv("EXTRACTOR_MODEL", "gpt-4o-mini")
CRITIC_MODEL = os.getenv("CRITIC_MODEL", "gpt-4o-mini")
GENERATOR_MODEL = os.getenv("GENERATOR_MODEL", "gpt-4o")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "85"))
MAX_GENERATOR_ITERATIONS = int(os.getenv("MAX_GENERATOR_ITERATIONS", "3"))
MAX_TEX_FIX_RETRIES = int(os.getenv("MAX_TEX_FIX_RETRIES", "2"))
TECTONIC_TIMEOUT_SECONDS = int(os.getenv("TECTONIC_TIMEOUT_SECONDS", "600"))
TOP_K_FACTS = int(os.getenv("TOP_K_FACTS", "10"))

FACT_STORE_PATH = os.getenv("FACT_STORE_PATH", "service/fact_store/facts.json")
SKELETON_PATH = os.getenv("SKELETON_PATH", "service/fact_store/resume_skeleton.json")
EMBEDDING_CACHE_PATH = os.getenv("EMBEDDING_CACHE_PATH", "service/fact_store/embeddings_cache.json")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
TEX_TEMPLATE_PATH = os.getenv("TEX_TEMPLATE_PATH", "service/latex/jake_template.tex.jinja")
MAX_BULLETS_PER_ENTRY = int(os.getenv("MAX_BULLETS_PER_ENTRY", "4"))
