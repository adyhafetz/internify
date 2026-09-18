from __future__ import annotations

import instructor
from openai import OpenAI

from service import config

# Single shared raw OpenAI client — used wherever we need plain chat/embeddings.
openai_client: OpenAI = OpenAI(api_key=config.OPENAI_API_KEY)

# Instructor-wrapped client — used wherever structured (Pydantic) output is needed.
instructor_client = instructor.from_openai(openai_client)

