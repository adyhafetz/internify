from __future__ import annotations

import instructor
from openai import OpenAI

from service import config

# Single shared raw OpenAI client — used wherever we need plain chat/embeddings.
openai_client: OpenAI = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.AI_BASE_URL)

# Instructor-wrapped client — used wherever structured (Pydantic) output is needed.
# Gemini answers structured requests most reliably as plain JSON; OpenAI uses tool calls (the library default).
instructor_client = instructor.from_openai(openai_client, mode=instructor.Mode.JSON) if config.AI_PROVIDER == "gemini" \
    else instructor.from_openai(openai_client)

