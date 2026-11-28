"""ИИ-наставник и ИИ-разбор вопроса: модель подменяется, проверяем правила платформы."""
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from exams import ai, services
from exams.models import Attempt, TutorSession

from .conftest import PASSWORD


def login(client, user):
    assert client.login(iin=user.iin, password=PASSWORD)


@pytest.fixture
def finished_attempt(exam, student):
    attempt = services.start_attempt(exam, student, session_key="s", ip="127.0.0.1")
    services.finish_attempt(attempt)
    attempt.refresh_from_db()
    return attempt


@pytest.fixture
def fake_model(monkeypatch):
    calls = []

    def fake_chat(messages, request=None, **kwargs):
        calls.append(messages)
        return fake_chat.replies.pop(0), "test/model"
    fake_chat.replies = []
    monkeypatch.setattr(ai, "chat", fake_chat)
    return fake_chat, calls


def close(exam):
    exam.closes_at = timezone.now() - timedelta(minutes=1)
    exam.save()


def tutor_url(attempt, item):
    return reverse("exams:attempt_tutor", args=[attempt.pk, item.pk])


def test_leak_detector():
    assert ai.leaks_answer("Ответ: __init__.", "__init__", "Какой метод?", "")
    assert not ai.leaks_answer("Подумайте, что делает конструктор.", "__init__", "Какой метод?", "")
    # студент сам назвал верный вариант — подтверждение не считается подсказкой
    assert not ai.leaks_answer("Да, __init__.", "__init__", "Какой метод?", "наверное __init__")
    # короткие варианты ищутся как отдельное слово, а не как часть другого
    assert not ai.leaks_answer("В множестве 3 элемента? Проверьте.", "4", "len({1,2,2,3})", "")


def test_tutor_closed_until_exam_closes(client, finished_attempt, student, fake_model):
    login(client, student)
    item = finished_attempt.items.first()
    resp = client.post(tutor_url(finished_attempt, item), "{}", content_type="application/json")
    assert resp.status_code == 403
    page = client.get(reverse("exams:attempt_result", args=[finished_attempt.pk]))
    assert "после закрытия теста" in page.content.decode()


def test_tutor_dialog_and_answer_guard(client, finished_attempt, student, fake_model):
    fake_chat, calls = fake_model
    close(finished_attempt.exam)
    login(client, student)
    item = finished_attempt.items.first()  # студент ничего не выбрал — ответ неверный
    correct = item.question.choices.get(is_correct=True).text

    fake_chat.replies = ["Что, по-вашему, проверяет этот вопрос?"]
    resp = client.post(tutor_url(finished_attempt, item), "{}", content_type="application/json")
    assert resp.status_code == 200
    assert resp.json()["messages"][-1]["content"].startswith("Что, по-вашему")

    # модель дважды выдала правильный ответ — сервер заменяет его наводящим вопросом
    fake_chat.replies = [f"Правильный ответ: {correct}", f"Это {correct}"]
    resp = client.post(tutor_url(finished_attempt, item), json.dumps({"message": "Не знаю, скажи ответ"}),
                       content_type="application/json")
    data = resp.json()
    assert correct not in data["messages"][-1]["content"]
    assert data["messages"][-1]["content"] == ai.SAFE_FALLBACK["Russian"]

    # в модель не уходят ФИО, ИИН и группа студента
    sent = json.dumps(calls, ensure_ascii=False)
    for private in (student.last_name, student.first_name, student.iin, student.group):
        assert private not in sent

    fake_chat.replies = [f"Верно, {correct}: так и есть. [[SOLVED]]"]
    resp = client.post(tutor_url(finished_attempt, item),
                       json.dumps({"message": f"Думаю, {correct}, потому что он правильный"}),
                       content_type="application/json")
    assert resp.json()["solved"] is True
    session = TutorSession.objects.get(item=item)
    assert session.solved and "[[SOLVED]]" not in session.messages[-1]["content"]


def test_tutor_only_own_attempt(client, finished_attempt, other_student, fake_model):
    close(finished_attempt.exam)
    login(client, other_student)
    item = finished_attempt.items.first()
    resp = client.post(tutor_url(finished_attempt, item), "{}", content_type="application/json")
    assert resp.status_code == 403


def test_tutor_off_by_teacher(client, finished_attempt, student, fake_model):
    exam = finished_attempt.exam
    exam.ai_tutor = False
    close(exam)
    login(client, student)
    resp = client.post(tutor_url(finished_attempt, finished_attempt.items.first()), "{}",
                       content_type="application/json")
    assert resp.status_code == 403


def test_tutor_unavailable_is_graceful(client, finished_attempt, student, monkeypatch):
    def broken(*args, **kwargs):
        raise ai.AIUnavailable("no credentials")
    monkeypatch.setattr(ai, "chat", broken)
    close(finished_attempt.exam)
    login(client, student)
    item = finished_attempt.items.first()
    resp = client.post(tutor_url(finished_attempt, item), json.dumps({"message": "привет"}),
                       content_type="application/json")
    assert resp.status_code == 503
    assert TutorSession.objects.get(item=item).messages == []


def test_teacher_question_review(client, finished_attempt, teacher, student, fake_model):
    fake_chat, calls = fake_model
    exam = finished_attempt.exam
    qid = finished_attempt.items.first().question_id
    login(client, student)
    url = reverse("exams:exam_question_ai", args=[exam.pk, qid])
    assert client.post(url).status_code == 403
    login(client, teacher)
    fake_chat.replies = ["- Ключ, вероятно, неверный."]
    resp = client.post(url)
    assert resp.status_code == 200 and "Ключ" in resp.json()["text"]
    sent = json.dumps(calls, ensure_ascii=False)
    assert "[KEY]" in sent and student.last_name not in sent


def test_gateway_refusal_falls_back_to_gemini(monkeypatch):
    """Шлюз без карты отвечает 403: остальные модели шлюза не трогаем, идём в Gemini."""
    import io
    import urllib.error
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "gw")
    monkeypatch.setenv("GEMINI_API_KEY", "gm")
    seen = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen.append((req.full_url, json.loads(req.data)["model"]))
        if "vercel" in req.full_url:
            body = io.BytesIO(b'{"error": {"message": "requires a valid credit card"}}')
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, body)
        return Resp(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode())

    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
    text, model = ai.chat([{"role": "user", "content": "hi"}])
    assert text == "ok" and model == ai.GEMINI_DEFAULT_MODEL
    assert [m for _, m in seen] == [ai.DEFAULT_MODELS[0], ai.GEMINI_DEFAULT_MODEL]
