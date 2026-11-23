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


def test_profile_page_shows_stats(client, student):
    login(client, student)
    r = client.get(reverse("accounts:profile"))
    html = r.content.decode()
    assert r.status_code == 200
    assert "тестов сдано" in html and student.masked_iin in html


def test_avatar_upload_is_square_and_served_from_database(client, student):
    login(client, student)
    url = reverse("accounts:profile")
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
    client.post(reverse("accounts:profile"), {"action": "avatar", "avatar": png()})
    student.refresh_from_db()
    client.logout()
    assert client.get(student.avatar.url).status_code == 302


def test_student_cannot_rename_but_can_change_email(client, student):
    login(client, student)
    client.post(reverse("accounts:profile"), {
        "action": "profile", "last_name": "Хакеров", "first_name": "Иван",
        "patronymic": "", "email": "new@example.com"})
    student.refresh_from_db()
    assert student.last_name != "Хакеров" and student.email == "new@example.com"


def test_teacher_can_rename(client, teacher):
    login(client, teacher)
    client.post(reverse("accounts:profile"), {
        "action": "profile", "last_name": "Новая", "first_name": "Фамилия",
        "patronymic": "", "email": ""})
    teacher.refresh_from_db()
    assert teacher.last_name == "Новая"


def test_password_change_keeps_session(client, student):
    login(client, student)
    r = client.post(reverse("accounts:profile"), {
        "action": "password", "old_password": PASSWORD,
        "new_password1": "Kyzylorda-2026", "new_password2": "Kyzylorda-2026"})
    assert r.status_code == 302
    assert client.get(reverse("accounts:profile")).status_code == 200
    student.refresh_from_db()
    assert student.check_password("Kyzylorda-2026")


def test_interface_language_is_saved_and_applied(client, student):
    login(client, student)
    r = client.post(reverse("accounts:profile"), {"action": "interface", "interface_language": "kk"})
    student.refresh_from_db()
    assert student.interface_language == "kk"
    assert r.cookies["django_language"].value == "kk"
    assert "Жеке кабинет" in client.get(reverse("accounts:profile")).content.decode()


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
