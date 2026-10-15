from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
WEB_DIR = PROJECT_ROOT / "autocann" / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"
LOGS_DIR = PROJECT_ROOT / "logs"

#: Overridable so migrations and reports can be run against a copy pulled from
#: production without touching the working database.
DB_PATH = Path(os.environ["AUTOCANN_DB"]).expanduser() if os.getenv("AUTOCANN_DB") \
    else DATA_DIR / "autocann.db"

