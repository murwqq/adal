"""Проверка веб-слоя через тестовый клиент Django (сервер не запускается)."""
import json

import pytest
from django.urls import reverse

from accounts import captcha
from accounts.models import User
from exams.models import Attempt, Question
from tests.conftest import PASSWORD, _iin


def login(client, user):
    assert client.login(iin=user.iin, password=PASSWORD)


def post_json(client, url, data=None):
    return client.post(url, json.dumps(data or {}), content_type="application/json")


# ---------- Вход и регистрация ----------

def test_login_by_iin_and_lockout(client, student):
    url = reverse("accounts:login")
    r = client.post(url, {"iin": student.iin, "password": "wrong"})
    assert "Неверный ИИН или пароль" in r.content.decode()
    for _ in range(5):
        client.post(url, {"iin": student.iin, "password": "wrong"})
    r = client.post(url, {"iin": student.iin, "password": PASSWORD})
    assert "Слишком много" in r.content.decode()


def test_login_success_and_unsafe_next(client, student):
    r = client.post(reverse("accounts:login") + "?next=https://evil.example/",
                    {"iin": student.iin, "password": PASSWORD})
    assert r.status_code == 302 and r.url == "/"


def test_anonymous_redirected_to_login(client, db):
    r = client.get("/")
    assert r.status_code == 302 and "/accounts/login/" in r.url


def test_registration_with_captcha(client, db):
    assert client.get(reverse("accounts:captcha"))["Content-Type"] == "image/png"
    code = client.session[captcha.SESSION_KEY]
    iin = _iin("0506106")
    data = {
        "interface_language": "ru", "degree": "bachelor", "application_type": "first",
        "citizenship": "Казахстан", "doc_type": "id_card", "doc_number": "045123456",
        "study_language": "kk", "iin": iin, "last_name": "Абитуриентова",
        "first_name": "Аружан", "patronymic": "", "email": "a@example.kz",
        "password1": "Strong-pass-77", "password2": "Strong-pass-77",
        "captcha": code.lower(), "terms": "on",
    }
    r = client.post(reverse("accounts:register"), data)
    assert r.status_code == 302
    user = User.objects.get(iin=iin)
    assert user.role == User.Role.STUDENT and user.birth_date.year == 2005
    assert user.check_password("Strong-pass-77")


def test_registration_errors(client, student):
    client.get(reverse("accounts:captcha"))
    r = client.post(reverse("accounts:register"), {
        "iin": student.iin, "password1": "abc", "password2": "abd", "captcha": "XXXXX"})
    html = r.content.decode()
    assert "уже зарегистрирован" in html and "Пароли не совпадают" in html
    assert "Символы не совпадают" in html and "Без согласия" in html


# ---------- Права доступа ----------

@pytest.mark.parametrize("name", ["exams:question_list", "exams:exam_list",
                                  "exams:question_import"])
def test_student_cannot_open_teacher_pages(client, student, name):
    login(client, student)
    assert client.get(reverse(name)).status_code == 403


def test_teacher_pages_render(client, teacher, exam):
    login(client, teacher)
    for url in ["/", reverse("exams:question_list") + "?q=вопрос&sort=hard",
                reverse("exams:question_new"), reverse("exams:exam_new"),
                reverse("exams:exam_list"), reverse("exams:exam_results", args=[exam.pk]),
                reverse("exams:exam_analysis", args=[exam.pk]),
                reverse("exams:exam_monitor", args=[exam.pk])]:
        assert client.get(url).status_code == 200, url


def test_other_teacher_cannot_see_results(client, exam, db):
    other = User.objects.create_user(_iin("7505054"), PASSWORD, first_name="А",
                                     last_name="Б", role=User.Role.TEACHER)
    login(client, other)
    assert client.get(reverse("exams:exam_results", args=[exam.pk])).status_code == 403


# ---------- Банк вопросов через интерфейс ----------

def test_create_question_via_form(client, teacher, subject):
    login(client, teacher)
    r = client.post(reverse("exams:question_new"), {
        "subject": subject.pk, "topic": "Формулы", "difficulty": 2,
        "text": "Чему равно $\\frac{1}{2}+\\frac{1}{2}$?", "correct": 1,
        "choice_0": "0", "choice_1": "1", "choice_2": "2"})
    assert r.status_code == 302
    q = Question.objects.get(topic="Формулы")
    assert q.choices.get(is_correct=True).text == "1"


def test_import_upload(client, teacher, subject):
    login(client, teacher)
    from django.core.files.uploadedfile import SimpleUploadedFile
    f = SimpleUploadedFile("q.txt", "<question>Q?\n<variant>да\n<variant>нет".encode())
    r = client.post(reverse("exams:question_import"),
                    {"subject": subject.pk, "difficulty": 1, "file": f})
    assert "сохранено: <b>1</b>" in r.content.decode()


# ---------- Полный сценарий прохождения ----------

def test_full_exam_flow(client, student, exam, teacher):
    login(client, student)
    r = client.post(reverse("exams:exam_start", args=[exam.pk]), {"code": "nope"})
    assert "Неверный код" in r.content.decode()
    r = client.post(reverse("exams:exam_start", args=[exam.pk]), {"code": "abc123"})
    attempt = Attempt.objects.get(student=student)
    assert r.url == reverse("exams:attempt_take", args=[attempt.pk])

    page = client.get(r.url).content.decode()
    assert "Вопрос 1 из 4" in page
    fragment = client.get(reverse("exams:attempt_question", args=[attempt.pk, 2]))
    assert "Вопрос 2 из 4" in fragment.content.decode()

    for item in attempt.items.all():
        correct = item.question.choices.get(is_correct=True)
        res = post_json(client, reverse("exams:attempt_answer", args=[attempt.pk]),
                        {"position": item.position, "choice": correct.id})
        assert res.json()["ok"] is True
    bad = post_json(client, reverse("exams:attempt_answer", args=[attempt.pk]),
                    {"position": 1, "choice": 999999})
    assert bad.status_code == 409

    ping = post_json(client, reverse("exams:attempt_ping", args=[attempt.pk])).json()
    assert ping["status"] == "active" and ping["seconds_left"] > 0
    ev = post_json(client, reverse("exams:attempt_event", args=[attempt.pk]),
                   {"kind": "tab_hidden"}).json()
    assert ev["violations"] == 1 and ev["terminated"] is False

    fin = client.post(reverse("exams:attempt_finish", args=[attempt.pk]),
                      HTTP_ACCEPT="application/json")
    assert fin.json()["redirect"].endswith("/result/")
    result = client.get(reverse("exams:attempt_result", args=[attempt.pk])).content.decode()
    assert "4</b><span>из 4" in result
    # Повторно начать нельзя — попадаем на результат.
    again = client.get(reverse("exams:attempt_take", args=[attempt.pk]))
    assert again.status_code == 302 and again.url.endswith("/result/")

    client.logout()
    login(client, teacher)
    data = client.get(reverse("exams:exam_monitor_data", args=[exam.pk])).json()
    assert data["rows"][0]["violations"] == 1
    csv_resp = client.get(reverse("exams:exam_results", args=[exam.pk]) + "?export=csv")
    assert "Касымов" in csv_resp.content.decode("utf-8-sig")


def test_attempt_in_other_browser_blocked_until_resume(client, student, exam, teacher):
    login(client, student)
    client.post(reverse("exams:exam_start", args=[exam.pk]), {"code": "ABC123"})
    attempt = Attempt.objects.get(student=student)
    client.logout()
    login(client, student)  # новая сессия = «другой компьютер»
    r = client.get(reverse("exams:attempt_take", args=[attempt.pk]))
    assert r.status_code == 409
    client.logout()
    login(client, teacher)
    client.post(reverse("exams:attempt_manage", args=[attempt.pk]), {"action": "resume"})
    client.logout()
    login(client, student)
    assert client.get(reverse("exams:attempt_take", args=[attempt.pk])).status_code == 200


def test_student_cannot_touch_foreign_attempt(client, student, other_student, exam):
    login(client, other_student)
    client.post(reverse("exams:exam_start", args=[exam.pk]), {"code": "ABC123"})
    attempt = Attempt.objects.get(student=other_student)
    client.logout()
    login(client, student)
    assert client.get(reverse("exams:attempt_take", args=[attempt.pk])).status_code == 403
