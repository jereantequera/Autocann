from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
WEB_DIR = PROJECT_ROOT / "autocann" / "web"
TEMPLATES_DIR = WEB_DIR / "templates"
LOGS_DIR = PROJECT_ROOT / "logs"

DB_PATH = DATA_DIR / "autocann.db"

