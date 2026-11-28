"""Текст из силлабуса для генерации вопросов: .docx, .pdf или .txt."""
from __future__ import annotations

import io
import re

from django.utils.translation import gettext_lazy as _

# Столько символов силлабуса отправляем в модель. Типичный силлабус курса — 10–40 тысяч.
MAX_CHARS = 60_000


class SyllabusError(Exception):
    """Файл не прочитать. Текст понятен пользователю."""


def _docx_text(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                text = cell.text.strip()
                if text and (not cells or cells[-1] != text):  # объединённые ячейки повторяются
                    cells.append(text)
            parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise SyllabusError(_("PDF защищён паролем. Сохраните его без пароля и загрузите снова"))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except PdfReadError as exc:
        raise SyllabusError(_("Не удалось прочитать PDF")) from exc


def _plain_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SyllabusError(_("Не удалось определить кодировку текстового файла"))


def normalize(text: str) -> str:
    text = text.replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_text(name: str, data: bytes) -> str:
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext == "docx":
        try:
            text = _docx_text(data)
        except Exception as exc:  # повреждённый архив, не Word-файл
            raise SyllabusError(_("Не удалось прочитать файл Word")) from exc
    elif ext == "pdf":
        text = _pdf_text(data)
    elif ext in ("txt", "md"):
        text = _plain_text(data)
    else:
        raise SyllabusError(_("Подходят файлы .docx, .pdf и .txt"))
    text = normalize(text)
    if len(text) < 200:
        raise SyllabusError(_("В файле слишком мало текста. Если это скан, загрузите версию с текстом"))
    return text[:MAX_CHARS]
