"""Регистронезависимый поиск по кириллице.

Встроенные LOWER и LIKE в SQLite понимают регистр только у латиницы, поэтому
«производная» не находила «Производная». Регистрируем в SQLite функцию на
Python (str.lower знает все алфавиты, включая казахский) и используем её в поиске.
В PostgreSQL обычный LOWER работает правильно, там подмена не нужна."""
from django.db.backends.signals import connection_created
from django.db.models.functions import Lower

SQLITE_FUNCTION = "py_lower"


def _py_lower(value):
    return value.lower() if isinstance(value, str) else value


def register_sqlite_lower(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        connection.connection.create_function(SQLITE_FUNCTION, 1, _py_lower, deterministic=True)


connection_created.connect(register_sqlite_lower)


class ULower(Lower):
    """LOWER, который на SQLite правильно работает с кириллицей."""

    def as_sqlite(self, compiler, connection, **extra_context):
        return super().as_sql(compiler, connection, function=SQLITE_FUNCTION, **extra_context)
