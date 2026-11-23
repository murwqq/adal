from datetime import timedelta

import pytest
from django.utils import timezone

from exams import services
from exams.models import Attempt, Exam, Question, Subject
from exams.services import ExamError


def start(exam, student, key="s1", now=None):
    return services.start_attempt(exam, student, session_key=key, ip="10.0.0.5", now=now)


# ---------- Случайные варианты ----------

def test_build_variant_is_deterministic_and_shuffles():
    qids = list(range(1, 21))
    choices = {q: [q * 10 + i for i in range(4)] for q in qids}
    a = services.build_variant(qids, choices, 5, seed=42)
    assert a == services.build_variant(qids, choices, 5, seed=42)
    assert a != services.build_variant(qids, choices, 5, seed=43)
    assert len({q for q, _ in a}) == 5
    for qid, order in a:
        assert sorted(order) == choices[qid]


@pytest.mark.parametrize("count", [0, 7])
def test_build_variant_rejects_bad_count(count):
    with pytest.raises(ExamError):
        services.build_variant([1, 2, 3], {1: [1], 2: [2], 3: [3]}, count, seed=1)


def test_students_get_different_variants(exam, student, other_student):
    a = start(exam, student)
    b = start(exam, other_student)
    order_a = [(i.question_id, i.choice_order) for i in a.items.all()]
    order_b = [(i.question_id, i.choice_order) for i in b.items.all()]
    assert len(order_a) == exam.question_count
    assert order_a != order_b


def test_start_twice_returns_same_attempt(exam, student):
    assert start(exam, student).pk == start(exam, student).pk


def test_deadline_limited_by_exam_close(exam, student):
    exam.closes_at = timezone.now() + timedelta(minutes=3)
    exam.save()
    attempt = start(exam, student)
    assert attempt.deadline == exam.closes_at


def test_not_enough_questions(exam, student):
    exam.question_count = 50
    exam.save()
    with pytest.raises(ExamError, match="подходящих вопросов"):
        start(exam, student)


def test_pool_is_the_exams_own_questions(exam, subject, teacher):
    first = exam.questions.first()
    services.remove_from_exam(exam, first)
    assert services.question_pool(exam).count() == 5
    other, _ = services.save_question(subject=subject, author=teacher, text="Только в банке",
                                      choices=[("да", True), ("нет", False)])
    assert other not in services.question_pool(exam)
    assert services.add_from_bank(exam, [other.pk, first.pk]) == 2
    assert services.question_pool(exam).count() == 7


def test_question_written_inside_exam(exam, subject, teacher):
    question, _ = services.save_question(subject=subject, author=teacher, text="Новый вопрос",
                                         choices=[("да", True), ("нет", False)], exam=exam)
    assert question in exam.questions.all() and question.is_active


# ---------- Удаление ----------

def test_delete_exam_with_results(exam, student):
    attempt = start(exam, student)
    with pytest.raises(ExamError, match="пишут"):
        services.delete_exam(exam)
    services.finish_attempt(attempt)
    services.delete_exam(exam)
    assert not Exam.objects.filter(pk=exam.pk).exists()
    assert not Attempt.all_objects.filter(pk=attempt.pk).exists()


def test_delete_subject(exam, subject):
    with pytest.raises(ExamError, match="тесты"):
        services.delete_subject(subject)
    exam.delete()
    services.delete_subject(subject)
    assert not Subject.objects.filter(pk=subject.pk).exists()
    assert not Question.objects.filter(subject_id=subject.pk).exists()


# ---------- Пересдача ----------

def test_retake_after_exam_closed(exam, student, teacher):
    now = timezone.now()
    first = start(exam, student, now=now)
    answer_all(first, correct=1)
    services.finish_attempt(first, now=now)
    exam.closes_at = now + timedelta(minutes=1)
    exam.save()
    later = now + timedelta(hours=3)
    with pytest.raises(ExamError, match="закончилось"):
        services.check_access(exam, student, code="ABC123", ip=None, now=later)

    services.grant_retake(exam, student, granted_by=teacher, expires_at=later + timedelta(hours=1), now=now)
    services.check_access(exam, student, code="ABC123", ip=None, now=later)
    second = services.start_attempt(exam, student, session_key="s2", ip=None, now=later)
    assert second.pk != first.pk
    assert second.deadline == later + timedelta(minutes=exam.duration_minutes)
    first.refresh_from_db()
    assert first.archived
    assert list(exam.attempts.all()) == [second]
    # Разрешение одноразовое: третий раз начать нельзя.
    services.finish_attempt(second, now=later)
    assert services.active_retake(exam, student, later) is None
    assert services.start_attempt(exam, student, session_key="s3", ip=None, now=later) == second


def test_retake_rules(exam, student, teacher):
    now = timezone.now()
    with pytest.raises(ExamError, match="будущем"):
        services.grant_retake(exam, student, granted_by=teacher, expires_at=now - timedelta(minutes=1))
    start(exam, student)
    with pytest.raises(ExamError, match="пишет"):
        services.grant_retake(exam, student, granted_by=teacher, expires_at=now + timedelta(hours=1))


# ---------- Доступ ----------

def test_access_checks(exam, student):
    services.check_access(exam, student, code="abc123", ip="1.2.3.4")  # регистр не важен
    with pytest.raises(ExamError, match="код"):
        services.check_access(exam, student, code="WRONG", ip=None)
    exam.groups = "ИС-22"
    with pytest.raises(ExamError, match="группе"):
        services.check_access(exam, student, code="ABC123", ip=None)
    exam.groups = "ис-21, ИС-22"
    exam.allowed_ip_prefix = "10.12.5."
    with pytest.raises(ExamError, match="аудитории"):
        services.check_access(exam, student, code="ABC123", ip="192.168.1.2")
    services.check_access(exam, student, code="ABC123", ip="10.12.5.40")


def test_access_time_window(exam, student):
    now = timezone.now()
    with pytest.raises(ExamError, match="не открыт"):
        services.check_access(exam, student, code="ABC123", ip=None, now=now - timedelta(hours=1))
    with pytest.raises(ExamError, match="закончилось"):
        services.check_access(exam, student, code="ABC123", ip=None, now=now + timedelta(hours=2))
    exam.is_published = False
    with pytest.raises(ExamError, match="опубликован"):
        services.check_access(exam, student, code="ABC123", ip=None)


# ---------- Ответы, таймер, баллы ----------

def answer_all(attempt, correct: int):
    for item in attempt.items.all():
        choices = item.question.choices.all()
        pick = next(c for c in choices if c.is_correct == (item.position <= correct))
        services.save_answer(attempt, item.position, pick.id)


def test_scoring(exam, student):
    attempt = start(exam, student)
    answer_all(attempt, correct=3)
    services.finish_attempt(attempt)
    attempt.refresh_from_db()
    assert (attempt.score, attempt.max_score, attempt.percent) == (3, 4, 75)
    assert attempt.status == Attempt.Status.FINISHED


def test_foreign_choice_and_position_rejected(exam, student, subject):
    attempt = start(exam, student)
    used = set(attempt.items.values_list("question_id", flat=True))
    foreign = Question.objects.exclude(id__in=used).first().choices.first()
    with pytest.raises(ExamError, match="нет у этого"):
        services.save_answer(attempt, 1, foreign.id)
    with pytest.raises(ExamError, match="нет в вашем"):
        services.save_answer(attempt, 99, None)


def test_clearing_answer(exam, student):
    attempt = start(exam, student)
    item = attempt.items.first()
    services.save_answer(attempt, 1, item.choice_order[0])
    item = services.save_answer(attempt, 1, None)
    assert item.selected_id is None and item.answered_at is None


def test_answer_after_deadline_expires_attempt(exam, student):
    attempt = start(exam, student)
    late = attempt.deadline + timedelta(seconds=services.GRACE_SECONDS + 1)
    with pytest.raises(ExamError, match="завершён"):
        services.save_answer(attempt, 1, None, now=late)
    attempt.refresh_from_db()
    assert attempt.status == Attempt.Status.EXPIRED


def test_answer_within_grace_is_accepted(exam, student):
    attempt = start(exam, student)
    item = attempt.items.first()
    services.save_answer(attempt, 1, item.choice_order[0], now=attempt.deadline + timedelta(seconds=2))


def test_seconds_left(exam, student):
    attempt = start(exam, student)
    assert services.seconds_left(attempt, now=attempt.deadline + timedelta(minutes=1)) == 0
    assert 590 <= services.seconds_left(attempt) <= 600


def test_finish_twice_keeps_first_result(exam, student):
    attempt = start(exam, student)
    services.finish_attempt(attempt)
    services.finish_attempt(attempt, status=Attempt.Status.TERMINATED)
    assert attempt.status == Attempt.Status.FINISHED


# ---------- Нарушения и сессия ----------

def test_violations_debounced_and_terminate(exam, student):
    attempt = start(exam, student)
    t = timezone.now()
    assert services.register_violation(attempt, "window_blur", now=t) is False
    assert services.register_violation(attempt, "tab_hidden", now=t + timedelta(seconds=1)) is False
    assert attempt.violation_count == 1  # blur + скрытие вкладки = одно событие
    services.register_violation(attempt, "fullscreen_exit", now=t + timedelta(seconds=10))
    assert services.register_violation(attempt, "tab_hidden", now=t + timedelta(seconds=20))
    attempt.refresh_from_db()
    assert attempt.status == Attempt.Status.TERMINATED
    assert services.register_violation(attempt, "tab_hidden") is True


def test_right_click_and_copy_are_not_violations(exam, student):
    attempt = start(exam, student)
    t = timezone.now()
    for i, kind in enumerate(["context_menu", "copy", "paste", "devtools", "print_screen"] * 3):
        assert services.register_violation(attempt, kind, now=t + timedelta(seconds=10 * i)) is False
    attempt.refresh_from_db()
    assert attempt.violation_count == 0 and attempt.status == Attempt.Status.ACTIVE


def test_unknown_violation_kind(exam, student):
    with pytest.raises(ExamError):
        services.register_violation(start(exam, student), "hacking")


def test_other_session_blocked_and_resume(exam, student):
    attempt = start(exam, student, key="pc-1")
    services.ensure_same_session(attempt, "pc-1")
    with pytest.raises(ExamError, match="другом браузере"):
        services.ensure_same_session(attempt, "pc-2")
    assert attempt.violations.filter(kind="other_session").exists()
    services.allow_resume(attempt)
    services.ensure_same_session(attempt, "pc-2")
    attempt.refresh_from_db()
    assert attempt.session_key == "pc-2"
    services.finish_attempt(attempt)
    with pytest.raises(ExamError):
        services.allow_resume(attempt)


def test_stale_heartbeat(exam, student):
    attempt = start(exam, student)
    later = timezone.now() + timedelta(seconds=services.HEARTBEAT_STALE_SECONDS + 5)
    assert services.is_stale(attempt, now=later)
    services.heartbeat(attempt, now=later)
    attempt.refresh_from_db()
    assert not services.is_stale(attempt, now=later)


# ---------- Банк вопросов ----------

def test_save_question_validation(subject, teacher):
    with pytest.raises(ExamError, match="ровно один"):
        services.save_question(subject=subject, author=teacher, text="Q",
                               choices=[("a", True), ("b", True)])
    with pytest.raises(ExamError, match="Пустой"):
        services.save_question(subject=subject, author=teacher, text="   ",
                               choices=[("a", True), ("b", False)])
    q, warnings = services.save_question(
        subject=subject, author=teacher, text="  Что   такое  ООП? ",
        choices=[("Объектно-ориентированное программирование", True), ("Язык", False),
                 ("Сеть", False), ("", False)])
    assert q.text == "Что такое ООП?" and q.choices.count() == 3
    assert any("длиннее" in w for w in warnings)


def test_edit_used_question_keeps_choices(exam, student, teacher):
    attempt = start(exam, student)
    q = attempt.items.first().question
    ids = list(q.choices.values_list("id", flat=True))
    services.save_question(subject=q.subject, author=teacher, text="Новый текст", question=q,
                           choices=[("w", True), ("x", False), ("y", False), ("z", False)])
    assert list(q.choices.values_list("id", flat=True)) == ids
    with pytest.raises(ExamError, match="число вариантов"):
        services.save_question(subject=q.subject, author=teacher, text="t", question=q,
                               choices=[("w", True), ("x", False)])


def test_delete_question_soft_when_used(exam, student, subject):
    attempt = start(exam, student)
    used = attempt.items.first().question
    assert "скрыт" in services.delete_question(used)
    used.refresh_from_db()
    assert used.is_active is False
    unused = Question.objects.exclude(attempt_items__isnull=False).first()
    assert services.delete_question(unused) == "Вопрос удалён"


def test_save_imported_counts_errors(subject, teacher):
    from exams.importer import ImportedQuestion
    items = [ImportedQuestion(text="ok", choices=[("a", True), ("b", False)]),
             ImportedQuestion(text="bad", choices=[("a", True)])]
    saved, errors = services.save_imported(items, subject=subject, author=teacher)
    assert saved == 1 and "Вопрос 2" in errors[0]


# ---------- Поиск, фильтры, сортировка ----------

def test_search_questions(subject, exam, student):
    base = Question.objects.all()
    assert services.search_questions(base, text="номер 3").count() == 1
    assert services.search_questions(base, text="ВОПРОС НОМЕР").count() == 6  # кириллица и регистр
    assert services.search_questions(base, text="неверный B5").count() == 1  # по тексту ответа
    assert services.search_questions(base, topic="ооп").count() == 2
    assert services.search_questions(base, difficulty=3).count() == 2
    hard_first = services.search_questions(base, sort="hard")
    assert hard_first.first().difficulty == 3
    easy_first = services.search_questions(base, sort="easy")
    assert easy_first.first().difficulty == 1
    attempt = start(exam, student)
    answer_all(attempt, correct=0)
    weak = services.search_questions(base, sort="weak").first()
    assert weak.times_used == 1 and weak.times_correct == 0
    assert services.search_questions(base, sort="unknown").count() == 6


def test_search_results(exam, student, other_student):
    a = start(exam, student)
    answer_all(a, correct=4)
    services.finish_attempt(a)
    b = start(exam, other_student)
    services.register_violation(b, "tab_hidden")
    services.finish_attempt(b)
    qs = exam.attempts.all()
    assert services.search_results(qs, sort="score_desc").first() == a
    assert services.search_results(qs, sort="score_asc").first() == b
    assert list(services.search_results(qs, flagged=True)) == [b]
    assert list(services.search_results(qs, group="ис-21")) == [a]
    assert list(services.search_results(qs, text="Касым")) == [a]
    assert services.search_results(qs, status="active").count() == 0


def test_exam_report(exam, student, other_student):
    for s, correct in ((student, 4), (other_student, 1)):
        attempt = start(exam, s)
        answer_all(attempt, correct=correct)
        services.finish_attempt(attempt)
    report = services.exam_report(exam)
    assert report["summary"]["count"] == 2
    assert report["summary"]["mean"] == 62.5
    assert report["items"] and all(s.answered >= 1 for s in report["items"])
