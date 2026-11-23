"""Личный кабинет, аватарки и картинки вопросов, которые хранятся в базе."""
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from exams.models import Question
from files.models import StoredFile
from tests.conftest import PASSWORD
from tests.test_views import login


def png(width=600, height=400, color="red"):
    buf = BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return SimpleUploadedFile("photo.png", buf.getvalue(), content_type="image/png")


def test_student_profile_empty_state(client, student):
    login(client, student)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "Здесь появится ваш прогресс" in html
    assert "нарушен" not in html.lower()


def test_student_profile_progress(client, exam, student):
    from exams import services
    from tests.test_services import answer_all, start
    attempt = start(exam, student)
    answer_all(attempt, correct=2)
    services.finish_attempt(attempt)
    login(client, student)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "Средний результат" in html and exam.title in html and exam.subject.name in html


def test_teacher_profile(client, teacher, exam):
    login(client, teacher)
    html = client.get(reverse("accounts:profile")).content.decode()
    assert "Вопросов в банке" in html and exam.title in html


def test_avatar_upload_is_square_and_served_from_database(client, student):
    login(client, student)
    url = reverse("accounts:settings")
    r = client.post(url, {"action": "avatar", "avatar": png()})
    assert r.status_code == 302
    student.refresh_from_db()
    stored = StoredFile.objects.get(name=student.avatar.name)
    assert Image.open(BytesIO(bytes(stored.content))).size == (320, 320)
    image = client.get(student.avatar.url)
    assert image.status_code == 200 and image["Content-Type"] == "image/jpeg"

    client.post(url, {"action": "avatar_delete"})
    student.refresh_from_db()
    assert not student.avatar and not StoredFile.objects.exists()


def test_files_need_login(client, student):
    login(client, student)
    client.post(reverse("accounts:settings"), {"action": "avatar", "avatar": png()})
    student.refresh_from_db()
    client.logout()
    assert client.get(student.avatar.url).status_code == 302


def test_student_cannot_rename_but_can_change_email(client, student):
    login(client, student)
    client.post(reverse("accounts:settings"), {
        "action": "profile", "last_name": "Хакеров", "first_name": "Иван",
        "patronymic": "", "email": "new@example.com"})
    student.refresh_from_db()
    assert student.last_name != "Хакеров" and student.email == "new@example.com"


def test_teacher_can_rename(client, teacher):
    login(client, teacher)
    client.post(reverse("accounts:settings"), {
        "action": "profile", "last_name": "Новая", "first_name": "Фамилия",
        "patronymic": "", "email": ""})
    teacher.refresh_from_db()
    assert teacher.last_name == "Новая"


def test_password_change_keeps_session(client, student):
    login(client, student)
    r = client.post(reverse("accounts:settings"), {
        "action": "password", "old_password": PASSWORD,
        "new_password1": "Kyzylorda-2026", "new_password2": "Kyzylorda-2026"})
    assert r.status_code == 302
    assert client.get(reverse("accounts:profile")).status_code == 200
    student.refresh_from_db()
    assert student.check_password("Kyzylorda-2026")


def test_avatar_quick_change_returns_to_profile(client, student):
    login(client, student)
    r = client.post(reverse("accounts:settings"),
                    {"action": "avatar", "avatar": png(), "next": reverse("accounts:profile")})
    assert r.status_code == 302 and r["Location"] == reverse("accounts:profile")


def test_password_section_hidden_until_opened(client, student):
    login(client, student)
    html = client.get(reverse("accounts:settings")).content.decode()
    assert '<details class="setting-row setting-details" >' in html


def test_question_with_image_created_by_hand(client, teacher, subject):
    login(client, teacher)
    data = {"subject": subject.pk, "text": "Что на рисунке?", "topic": "", "difficulty": "2",
            "image": png(), "correct": "0"}
    for i, text in enumerate(["Квадрат", "Круг", "Треугольник", "Ромб"]):
        data[f"choice_{i}"] = text
    r = client.post(reverse("exams:question_new"), data)
    assert r.status_code == 302
    question = Question.objects.get(text="Что на рисунке?")
    assert question.image and client.get(question.image.url).status_code == 200
    bank = client.get(reverse("exams:question_list")).content.decode()
    assert question.image.url in bank
