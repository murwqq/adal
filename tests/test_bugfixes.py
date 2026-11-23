"""Баги из отчёта тестировщика: каждый закреплён тестом, чтобы не вернулся."""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from exams import services
from exams.analysis import percent, score_summary
from exams.forms import ExamForm, SubjectForm
from exams.models import Attempt, Exam, Question, Subject
from exams.services import ExamError
from tests.conftest import PASSWORD, _iin
from tests.test_services import answer_all, start
from tests.test_views import login


def test_percent_rounds_half_up():
    assert [percent(1, 8), percent(5, 8), percent(7, 8)] == [13, 63, 88]
    assert percent(0, 0) == 0


def test_median_always_has_one_decimal():
    assert score_summary([50, 70])["median"] == 60.0
    assert score_summary([50, 60, 70])["median"] == 60.0
    assert isinstance(score_summary([62])["median"], float)


def test_exam_list_counts_attempts_not_attempts_times_questions(client, teacher, exam, student, other_student):
    for s in (student, other_student):
        services.finish_attempt(start(exam, s, key=s.iin))
    login(client, teacher)
    html = client.get(reverse("exams:exam_list")).content.decode()
    assert '<td class="right num">2</td>' in html


def test_dashboard_shows_closing_date_with_year(client, teacher, exam):
    exam.closes_at = exam.opens_at + timedelta(days=400)
    exam.save()
    login(client, teacher)
    html = client.get(reverse("dashboard")).content.decode()
    assert timezone.localtime(exam.closes_at).strftime("%d.%m.%Y") in html


def test_overdue_attempt_closes_without_monitor(client, teacher, exam, student):
    attempt = start(exam, student)
    answer_all(attempt, correct=2)
    Attempt.objects.filter(pk=attempt.pk).update(deadline=timezone.now() - timedelta(minutes=1))
    login(client, teacher)
    client.get(reverse("exams:exam_results", args=[exam.pk]))
    attempt.refresh_from_db()
    assert attempt.status == Attempt.Status.EXPIRED and attempt.score == 2


def test_subject_names_are_unique_ignoring_case(db):
    Subject.objects.create(name="Физика")
    form = SubjectForm({"name": "  физика "})
    assert not form.is_valid()
    assert SubjectForm({"name": "Химия"}).is_valid()


def test_groups_are_cleaned(subject):
    now = timezone.localtime()
    form = ExamForm({"title": "Т", "subject": subject.pk, "question_count": 1, "duration_minutes": 5,
                     "opens_at": now.strftime("%Y-%m-%dT%H:%M"),
                     "closes_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                     "groups": "ИС-99, ,ис-99;  ис-21 ", "access_code": "X1", "max_violations": 3})
    assert form.is_valid(), form.errors
    assert form.cleaned_data["groups"] == "ИС-99, ИС-21"


def test_new_exam_is_a_draft_until_published(client, teacher, subject, student):
    login(client, teacher)
    now = timezone.localtime()
    client.post(reverse("exams:exam_new"), {
        "title": "Черновой", "subject": subject.pk, "question_count": 2, "duration_minutes": 5,
        "opens_at": now.strftime("%Y-%m-%dT%H:%M"), "source": "manual",
        "closes_at": (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        "groups": "", "access_code": "QQ1", "max_violations": 3})
    exam = Exam.objects.get(title="Черновой")
    assert not exam.is_published
    with pytest.raises(ExamError):
        services.set_published(exam, True)  # вопросов ещё нет
    exam.questions.set(subject.questions.all()[:2])
    client.post(reverse("exams:exam_questions", args=[exam.pk]), {"action": "publish"})
    exam.refresh_from_db()
    assert exam.is_published


def test_student_does_not_see_exam_without_enough_questions(client, exam, student):
    exam.questions.clear()
    login(client, student)
    assert exam.title not in client.get(reverse("dashboard")).content.decode()


def test_teacher_cannot_edit_or_delete_foreign_questions(client, teacher, subject):
    other = User.objects.create_user(_iin("6905123"), PASSWORD, first_name="Другой", last_name="Учитель",
                                     role=User.Role.TEACHER)
    q, _ = services.save_question(subject=subject, author=other, text="Чужой вопрос",
                                  choices=[("да", True), ("нет", False)])
    login(client, teacher)
    assert client.get(reverse("exams:question_edit", args=[q.pk])).status_code == 403
    assert client.post(reverse("exams:question_delete", args=[q.pk])).status_code == 403
    with pytest.raises(ExamError, match="других преподавателей"):
        services.delete_subject(subject, teacher)
    assert Question.objects.filter(pk=q.pk).exists()


def test_confirm_text_keeps_apostrophe(client, teacher):
    Subject.objects.create(name="QA-тест O'Neil")
    login(client, teacher)
    html = client.get(reverse("dashboard")).content.decode()
    assert 'data-confirm="Удалить дисциплину «QA-тест O&#x27;Neil»' in html


def test_save_and_add_another_keeps_topic(client, teacher, exam):
    login(client, teacher)
    r = client.post(reverse("exams:question_new"), {
        "exam": exam.pk, "text": "Первый", "topic": "Циклы", "difficulty": "3", "correct": "0",
        "choice_0": "а", "choice_1": "б", "add_another": "1"})
    assert "topic=" in r["Location"]
    html = client.get(r["Location"]).content.decode()
    assert 'value="Циклы"' in html
