from __future__ import annotations

import instructor
from openai import OpenAI

from service import config, metering

# Single shared raw OpenAI client — used wherever we need plain chat/embeddings.
openai_client: OpenAI = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.AI_BASE_URL)

# Instructor-wrapped client — used wherever structured (Pydantic) output is needed.
# Gemini answers structured requests most reliably as plain JSON; OpenAI uses tool calls (the library default).
instructor_client = instructor.from_openai(openai_client, mode=instructor.Mode.JSON) if config.AI_PROVIDER == "gemini" \
    else instructor.from_openai(openai_client)



def _counted(fn, kind: str):
    """The same call, counted for the acting user first (see service/metering.py)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        metering.count(kind)
        return fn(*args, **kwargs)
    return wrapper


instructor_client.chat.completions.create = _counted(instructor_client.chat.completions.create, "ai")
openai_client.embeddings.create = _counted(openai_client.embeddings.create, "ai")
