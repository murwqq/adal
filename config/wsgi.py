"""WSGI-точка входа: waitress/gunicorn используют `application`, Vercel — `app`."""
import os
import shutil
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

if os.environ.get("VERCEL"):
    # Демо-режим: файловая система Vercel только для чтения, кроме /tmp.
    # Копируем базу с демо-данными туда при холодном старте.
    demo_db = Path(__file__).resolve().parent.parent / "deploy" / "demo.sqlite3"
    tmp_db = Path("/tmp/adal.sqlite3")
    if not tmp_db.exists():
        shutil.copy(demo_db, tmp_db)
    os.environ.setdefault("DJANGO_DB_PATH", str(tmp_db))

application = get_wsgi_application()
app = application
