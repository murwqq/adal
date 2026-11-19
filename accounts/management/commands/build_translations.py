"""Сборка переводов без GNU gettext: python manage.py build_translations

1. Находит все строки для перевода в шаблонах ({% translate %}, {% blocktranslate trimmed %})
   и в Python-коде (_("..."), gettext(...), gettext_lazy(...)).
2. Берёт переводы из locale_catalog.py (русский текст → казахский, английский).
3. Пишет locale/<язык>/LC_MESSAGES/django.po и компилирует django.mo через polib.
Если для какой-то строки нет перевода, команда перечисляет такие строки и падает."""
import re
from pathlib import Path

import polib
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

TARGETS = ("kk", "en")
SKIP_DIRS = {".venv", "node_modules", "staticfiles", "media", ".git", "tests", "migrations"}

TRANSLATE_TAG = re.compile(r"""{%\s*(?:translate|trans)\s+(["'])(.*?)\1(?:\s+as\s+\w+)?\s*%}""", re.S)
BLOCK_TAG = re.compile(r"{%\s*blocktranslate\b([^%]*)%}(.*?){%\s*endblocktranslate\s*%}", re.S)
VARIABLE = re.compile(r"{{\s*([\w.]+)\s*}}")
PY_CALL = re.compile(r"""\b(?:_|gettext|gettext_lazy|gettext_noop)\(\s*"((?:[^"\\]|\\.)*)"\s*[,)]""")


def trim_whitespace(text: str) -> str:
    """То же, что делает Django для {% blocktranslate trimmed %}."""
    return re.sub(r"\s*\n\s*", " ", text.strip())


def block_msgid(options: str, body: str) -> str:
    if "trimmed" in options:
        body = trim_whitespace(body)
    body = body.replace("%", "%%")
    return VARIABLE.sub(lambda m: f"%({m.group(1)})s", body)


def extract(base: Path) -> set[str]:
    found: set[str] = set()
    for path in base.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts) or not path.is_file():
            continue
        if path.suffix == ".html":
            text = path.read_text(encoding="utf-8")
            found.update(m.group(2) for m in TRANSLATE_TAG.finditer(text))
            found.update(block_msgid(m.group(1), m.group(2)) for m in BLOCK_TAG.finditer(text))
        elif path.suffix == ".py" and "management" not in path.parts \
                and path.name != "locale_catalog.py":
            text = path.read_text(encoding="utf-8")
            found.update(m.group(1).replace('\\"', '"') for m in PY_CALL.finditer(text))
    return found


class Command(BaseCommand):
    help = "Собирает .po/.mo для казахского и английского из locale/translations.py"

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        from locale_catalog import TRANSLATIONS  # noqa: WPS433

        msgids = extract(base)
        missing = sorted(m for m in msgids if m not in TRANSLATIONS)
        if missing:
            listing = "\n".join(f'    "{m}": ("", ""),' for m in missing)
            raise CommandError(f"Нет перевода для {len(missing)} строк:\n{listing}")
        unused = sorted(set(TRANSLATIONS) - msgids)
        for index, lang in enumerate(TARGETS):
            po = polib.POFile()
            po.metadata = {"Content-Type": "text/plain; charset=UTF-8", "Language": lang,
                           "MIME-Version": "1.0", "Content-Transfer-Encoding": "8bit"}
            for msgid in sorted(msgids):
                po.append(polib.POEntry(msgid=msgid, msgstr=TRANSLATIONS[msgid][index]))
            out = base / "locale" / lang / "LC_MESSAGES"
            out.mkdir(parents=True, exist_ok=True)
            po.save(str(out / "django.po"))
            po.save_as_mofile(str(out / "django.mo"))
        self.stdout.write(self.style.SUCCESS(
            f"Готово: {len(msgids)} строк × {len(TARGETS)} языка"))
        if unused:
            self.stdout.write(f"В каталоге есть неиспользуемые строки: {len(unused)}")
