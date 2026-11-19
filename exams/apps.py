from django.apps import AppConfig


class ExamsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "exams"
    verbose_name = "Тестирование"

    def ready(self):
        from . import dbfuncs  # noqa: F401 — регистрирует py_lower для SQLite
