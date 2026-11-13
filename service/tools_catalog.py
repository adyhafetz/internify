"""One shared list of well-known tools: their proper name, their skill category, and the other names they go by
(spellings, package names). Used to read GitHub repos, to put skills in the right category, and to tidy the profile."""
from __future__ import annotations

import re
from typing import Optional

LANG, FW, DB, AI, DEV = "Languages", "Frameworks & Libraries", "Databases", "AI/ML", "Developer Tools & Infra"

# proper name: (category, other names and package names, all lower case)
TOOLS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Python": (LANG, ("python3", "py")), "JavaScript": (LANG, ("js", "javascript", "ecmascript")),
    "TypeScript": (LANG, ("ts", "typescript")), "Java": (LANG, ()), "Kotlin": (LANG, ()), "Swift": (LANG, ()),
    "C": (LANG, ()), "C++": (LANG, ("cpp", "c plus plus")), "C#": (LANG, ("csharp", "c sharp")), "Go": (LANG, ("golang",)),
    "Rust": (LANG, ()), "PHP": (LANG, ()), "Ruby": (LANG, ()), "Dart": (LANG, ()), "R": (LANG, ()), "SQL": (LANG, ()),
    "HTML": (LANG, ("html5",)), "CSS": (LANG, ("css3",)), "Bash": (LANG, ("shell", "shell script")),
    "React.js": (FW, ("react", "reactjs", "react js")), "React Native": (FW, ("react-native", "reactnative")),
    "Expo": (FW, ("expo",)), "Next.js": (FW, ("next", "nextjs", "next js")), "Vue.js": (FW, ("vue", "vuejs")),
    "Angular": (FW, ("@angular/core", "angularjs")), "Svelte": (FW, ("svelte", "sveltekit", "@sveltejs/kit")),
    "Node.js": (FW, ("node", "nodejs", "node js")), "Express": (FW, ("express", "expressjs", "express.js")),
    "NestJS": (FW, ("@nestjs/core", "nest.js", "nestjs")), "Tailwind CSS": (FW, ("tailwind", "tailwindcss")),
    "Bootstrap": (FW, ()), "jQuery": (FW, ("jquery",)), "Redux": (FW, ("redux", "@reduxjs/toolkit")),
    "Flutter": (FW, ()), "FastAPI": (FW, ("fastapi",)), "Django": (FW, ("django",)), "Flask": (FW, ("flask",)),
    "Spring Boot": (FW, ("spring", "springboot", "spring-boot")), "Laravel": (FW, ("laravel/framework", "laravel")),
    "Ruby on Rails": (FW, ("rails",)), ".NET": (FW, ("dotnet", "asp.net")), "Streamlit": (FW, ("streamlit",)),
    "SQLAlchemy": (FW, ("sqlalchemy",)), "Prisma": (FW, ("prisma", "@prisma/client")), "Pydantic": (FW, ("pydantic",)),
    "Pandas": (AI, ("pandas",)), "NumPy": (AI, ("numpy",)), "Scikit-learn": (AI, ("sklearn", "scikit-learn", "scikit learn")),
    "TensorFlow": (AI, ("tensorflow", "tf")), "TensorFlow Lite": (AI, ("tflite", "tensorflow lite", "tflite-runtime")),
    "PyTorch": (AI, ("torch", "pytorch")), "Keras": (AI, ("keras",)), "OpenCV": (AI, ("opencv-python", "cv2", "opencv")),
    "Hugging Face Transformers": (AI, ("transformers", "huggingface")), "LangChain": (AI, ("langchain",)),
    "OpenAI API": (AI, ("openai",)), "Anthropic API": (AI, ("anthropic",)), "YOLOv8": (AI, ("ultralytics", "yolo", "yolov8")),
    "Matplotlib": (AI, ("matplotlib",)), "Jupyter": (AI, ("jupyter", "notebook", "ipykernel")),
    "PostgreSQL": (DB, ("postgres", "postgresql", "psycopg2", "psycopg2-binary", "pg", "asyncpg", "psycopg")),
    "MySQL": (DB, ("mysql", "mysql2", "pymysql", "mysqlclient")), "SQLite": (DB, ("sqlite", "sqlite3", "better-sqlite3")),
    "MongoDB": (DB, ("mongo", "mongodb", "mongoose", "pymongo")), "Redis": (DB, ("redis", "ioredis")),
    "Firebase": (DB, ("firebase", "firebase-admin")), "Supabase": (DB, ("supabase", "@supabase/supabase-js")),
    "Oracle Database": (DB, ("oracle", "cx_oracle", "oracledb")), "Elasticsearch": (DB, ("elasticsearch",)),
    "Docker": (DEV, ("dockerfile",)), "Docker Compose": (DEV, ("docker-compose",)),
    "Kubernetes": (DEV, ("k8s", "kubectl")), "Git": (DEV, ()), "GitHub": (DEV, ()), "GitHub Actions": (DEV, ("gh actions",)),
    "Linux": (DEV, ()), "AWS": (DEV, ("amazon web services", "boto3")), "Google Cloud": (DEV, ("gcp",)),
    "Azure": (DEV, ()), "Vercel": (DEV, ()), "Cloudflare": (DEV, ()), "Nginx": (DEV, ()), "Postman": (DEV, ()),
    "Jest": (DEV, ("jest",)), "Vitest": (DEV, ("vitest",)), "pytest": (DEV, ("pytest",)), "Vite": (DEV, ("vite",)),
    "Webpack": (DEV, ("webpack",)), "Celery": (DEV, ("celery",)), "Terraform": (DEV, ()), "Figma": (DEV, ()),
    "Maven": (DEV, ()), "Gradle": (DEV, ()), "LaTeX": (DEV, ("latex", "tex")), "VS Code": (DEV, ("vscode",)),
}

SOFT_SKILLS = {"teamwork", "communication", "leadership", "problem solving", "problem-solving", "time management",
               "critical thinking", "collaboration", "adaptability", "creativity", "public speaking", "hardworking",
               "fast learner", "attention to detail", "interpersonal skills", "self-motivated", "multitasking", "presentation"}

_KEY = re.compile(r"[^a-z0-9+#.]")


def key(name: str) -> str:
    return _KEY.sub("", name.lower().strip())


_INDEX: dict[str, str] = {}
for _name, (_cat, _aliases) in TOOLS.items():
    _INDEX[key(_name)] = _name
    for _a in _aliases:
        _INDEX.setdefault(key(_a), _name)


def canonical(name: str) -> Optional[str]:
    """The proper name of a well-known tool ("reactjs" -> "React.js"), or None if it isn't one we know."""
    return _INDEX.get(key(name)) if name.strip() else None


def category(name: str) -> Optional[str]:
    c = canonical(name)
    return TOOLS[c][0] if c else None


def mentions(tool: str, text: str) -> bool:
    """Does the text name this tool (by its proper name or one of its other names)?"""
    t = key(text)
    c = canonical(tool) or tool
    names = [c] + [a for a in TOOLS.get(c, ("", ()))[1] if len(a) >= 3]
    return any(key(n) and key(n) in t for n in names)


def is_soft_skill(name: str) -> bool:
    return name.strip().lower() in SOFT_SKILLS
