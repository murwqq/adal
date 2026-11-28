"""Вопросы из силлабуса и события расширения Адал Guard."""
import io
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from exams import ai, services
from exams.models import Attempt, Question, Violation
from exams.syllabus import SyllabusError, extract_text

from .conftest import PASSWORD

SYLLABUS = ("Силлабус дисциплины «Программирование на Python». Неделя 1: типы данных int, float, str. "
            "Неделя 2: условия и циклы for, while. Неделя 3: функции, аргументы, область видимости. "
            "Неделя 4: списки, словари, множества и их методы. Неделя 5: классы, конструктор __init__, "
            "наследование. Неделя 6: работа с файлами и исключения. Итоговый контроль: тест.") * 2


def login(client, user):
    assert client.login(iin=user.iin, password=PASSWORD)


def _docx(text: str) -> bytes:
    from docx import Document
    doc = Document()
    for line in text.split(". "):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_formats():
    assert "Неделя 5" in extract_text("syllabus.docx", _docx(SYLLABUS))
    assert "Неделя 5" in extract_text("syllabus.txt", SYLLABUS.encode("cp1251"))
    with pytest.raises(SyllabusError):
        extract_text("syllabus.txt", "Коротко".encode())
    with pytest.raises(SyllabusError):
        extract_text("syllabus.xlsx", b"PK")


def test_clean_generated_drops_bad_items():
    raw = {"questions": [
        {"text": "Что вернёт type(3 / 2)?", "options": ["int", "float", "str", "bool"], "correct": 1,
         "level": "apply", "topic": "Типы", "source": "Неделя 1", "why": "путают / и //"},
        {"text": "Без вариантов", "options": ["a", "b"], "correct": 0},
        {"text": "Повторы", "options": ["a", "a", "b", "c"], "correct": 0},
        {"text": "Ключ вне диапазона", "options": ["a", "b", "c", "d"], "correct": 7},
        {"text": "Что вернёт type(3 / 2)?", "options": ["1", "2", "3", "4"], "correct": 0},
    ]}
    result = ai.clean_generated(raw, 10)
    assert len(result) == 1 and result[0]["difficulty"] == 3 and result[0]["correct"] == 1


def test_generate_preview_and_save(client, teacher, subject, exam, monkeypatch):
    def fake_generate(**kwargs):
        assert "Неделя 5" in kwargs["syllabus"]
        return [{"topic": "ООП", "level": "understand", "difficulty": 2,
                 "text": "Какой метод вызывается при создании объекта?",
                 "options": ["__init__", "__del__", "__str__", "__len__"], "correct": 0,
                 "source": "конструктор __init__", "why": "путают с другими методами"},
                {"topic": "Python", "level": "remember", "difficulty": 1, "text": "Вопрос номер 1",
                 "options": ["a", "b", "c", "d"], "correct": 0, "source": "", "why": ""}], "test/model"
    monkeypatch.setattr(ai, "generate_questions", fake_generate)
    login(client, teacher)
    url = reverse("exams:question_generate")
    upload = SimpleUploadedFile("syllabus.docx", _docx(SYLLABUS))
    resp = client.post(url, {"subject": subject.pk, "file": upload, "count": 5, "language": "ru",
                             "exam": exam.pk})
    page = resp.content.decode()
    assert resp.status_code == 200
    assert "Какой метод вызывается при создании объекта?" in page
    assert "уже есть в банке" in page  # второй вопрос повторяет вопрос из банка

    before = Question.objects.count()
    resp = client.post(url, {
        "action": "save", "subject": subject.pk, "exam": exam.pk, "total": 2,
        "q0-keep": "1", "q0-text": "Какой метод вызывается при создании объекта?",
        "q0-o0": "__init__", "q0-o1": "__del__", "q0-o2": "__str__", "q0-o3": "__len__",
        "q0-correct": "0", "q0-topic": "ООП", "q0-difficulty": "2",
        "q1-text": "Вопрос номер 1", "q1-o0": "a", "q1-o1": "b", "q1-o2": "c", "q1-o3": "d", "q1-correct": "0",
    })
    assert resp.status_code == 302
    assert Question.objects.count() == before + 1
    q = Question.objects.latest("id")
    assert q.choices.get(is_correct=True).text == "__init__"
    assert exam.questions.filter(pk=q.pk).exists()


def test_generate_forbidden_for_students(client, student):
    login(client, student)
    assert client.get(reverse("exams:question_generate")).status_code == 403


@pytest.fixture
def active_attempt(exam, student):
    return services.start_attempt(exam, student, session_key="s", ip="127.0.0.1")


def test_guard_events(active_attempt):
    a = active_attempt
    # заметки видны преподавателю, но не приближают завершение теста
    assert services.register_violation(a, "new_tab") is False
    assert services.register_violation(a, "ai_extension", detail="ChatGPT Sidebar") is False
    a.refresh_from_db()
    assert a.violation_count == 0
    note = a.violations.get(kind="ai_extension")
    assert note.detail == "ChatGPT Sidebar" and note.counted is False
    # попытка открыть ИИ-сервис считается нарушением
    services.register_violation(a, "ai_site", detail="chatgpt.com")
    a.refresh_from_db()
    assert a.violation_count == 1


def test_guard_version_in_ping_and_monitor(client, active_attempt, student, teacher):
    from exams.views import BROWSER_ID
    login(client, student)
    session = client.session
    session[BROWSER_ID] = "browser-1"
    session.save()
    a = active_attempt
    Attempt.objects.filter(pk=a.pk).update(session_key="browser-1")
    resp = client.post(reverse("exams:attempt_ping", args=[a.pk]), json.dumps({"guard": "1.0.0"}),
                       content_type="application/json")
    assert resp.status_code == 200
    a.refresh_from_db()
    assert a.guard_version == "1.0.0"
    resp = client.post(reverse("exams:attempt_event", args=[a.pk]),
                       json.dumps({"kind": "ai_site", "detail": "gemini.google.com"}),
                       content_type="application/json")
    assert resp.json()["violations"] == 1
    login(client, teacher)
    rows = client.get(reverse("exams:exam_monitor_data", args=[a.exam_id])).json()["rows"]
    assert rows[0]["guard"] == "1.0.0" and "gemini.google.com" in rows[0]["last_violation"]


def test_guard_page_and_required_flag(client, exam, student):
    page = client.get(reverse("exams:guard"))
    assert page.status_code == 200 and "adal-guard.zip" in page.content.decode()
    privacy = client.get(reverse("exams:guard_privacy"))
    assert privacy.status_code == 200 and "Privacy policy" in privacy.content.decode()
    exam.require_guard = True
    exam.save()
    login(client, student)
    start = client.get(reverse("exams:exam_start", args=[exam.pk])).content.decode()
    assert "Адал Guard" in start


def test_guard_demo_exam_is_created_and_extended(db):
    from datetime import timedelta
    from django.core.management import call_command
    from django.utils import timezone
    from exams.management.commands.seed_demo import ensure_guard_exam
    call_command("seed_demo", verbosity=0)
    exam = ensure_guard_exam()
    assert exam.require_guard and exam.group_list == [] and exam.is_open()
    exam.closes_at = timezone.now() + timedelta(days=1)
    exam.save()
    ensure_guard_exam()
    exam.refresh_from_db()
    assert exam.closes_at > timezone.now() + timedelta(days=100)
