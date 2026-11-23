"""WSGI-точка входа: waitress/gunicorn используют `application`, Vercel — `app`."""
import os
import shutil
from pathlib import Path

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

USE_POSTGRES = bool(os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL"))

if os.environ.get("VERCEL") and not USE_POSTGRES:
    # Демо-режим без базы: файловая система Vercel только для чтения, кроме /tmp.
    # Копируем базу с демо-данными туда при холодном старте. Изменения живут,
    # пока жив экземпляр сервера.
    demo_db = Path(__file__).resolve().parent.parent / "deploy" / "demo.sqlite3"
    tmp_db = Path("/tmp/adal.sqlite3")
    if not tmp_db.exists():
        shutil.copy(demo_db, tmp_db)
    os.environ.setdefault("DJANGO_DB_PATH", str(tmp_db))

application = get_wsgi_application()
app = application

if os.environ.get("VERCEL") and USE_POSTGRES:
    # На Vercel нет шага сборки с доступом к базе, поэтому миграции и демо-данные
    # применяются при холодном старте. Блокировка не даёт двум экземплярам
    # делать это одновременно.
    from django.core.management import call_command
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(20261120)")
        try:
            call_command("migrate", interactive=False, verbosity=0)
            from accounts.models import User
            if not User.objects.exists():
                call_command("seed_demo", verbosity=0)
        finally:
            cursor.execute("SELECT pg_advisory_unlock(20261120)")
    connection.close()
