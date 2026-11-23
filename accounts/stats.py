"""Цифры для страницы профиля.

Студенту показываем его прогресс: средний и последний результат, динамику
по тестам и успехи по дисциплинам. Преподавателю показываем его банк вопросов
и то, как студенты справляются с его тестами. Служебные данные (нарушения,
дата регистрации) сюда намеренно не попадают."""
from collections import defaultdict

from django.urls import reverse


def _pct(score, max_score):
    return round(100 * score / max_score) if max_score else 0


def _avg(values):
    return round(sum(values) / len(values)) if values else None


def student_overview(user):
    from exams.models import Attempt
    done = list(
        Attempt.objects.filter(student=user).exclude(status=Attempt.Status.ACTIVE)
        .select_related("exam", "exam__subject").order_by("finished_at", "started_at"))
    percents = [a.percent for a in done]
    last = percents[-1] if percents else None
    delta = percents[-1] - percents[-2] if len(percents) > 1 else None

    subjects = defaultdict(list)
    for a in done:
        subjects[a.exam.subject.name].append(a.percent)
    by_subject = sorted(
        ({"name": name, "avg": _avg(values), "count": len(values)} for name, values in subjects.items()),
        key=lambda s: (-s["count"], s["name"]))

    return {
        "count": len(done),
        "avg": _avg(percents),
        "best": max(percents) if percents else None,
        "last": last,
        "delta": delta,
        "delta_abs": abs(delta) if delta is not None else None,
        "timeline": [
            {"label": a.exam.title, "value": a.percent,
             "date": a.finished_at or a.started_at,
             "url": reverse("exams:attempt_result", args=[a.pk])}
            for a in done[-10:]],
        "by_subject": by_subject,
        "recent": [
            {"title": a.exam.title, "subject": a.exam.subject.name, "date": a.finished_at or a.started_at,
             "percent": a.percent, "score": a.score, "max_score": a.max_score,
             
             "url": reverse("exams:attempt_result", args=[a.pk])}
            for a in reversed(done[-5:])],
        "active": Attempt.objects.filter(student=user, status=Attempt.Status.ACTIVE)
                                 .select_related("exam").first(),
    }


def teacher_overview(user):
    from exams.models import Attempt, Exam, Question
    exams = list(Exam.objects.filter(author=user).select_related("subject").order_by("opens_at"))
    works = defaultdict(list)
    for exam_id, score, max_score in (
            Attempt.objects.filter(exam__author=user).exclude(status=Attempt.Status.ACTIVE)
            .values_list("exam_id", "score", "max_score")):
        works[exam_id].append(_pct(score, max_score))
    all_works = [p for values in works.values() for p in values]

    bank = defaultdict(int)
    for name in Question.objects.filter(author=user, is_active=True).values_list("subject__name", flat=True):
        bank[name] += 1
    bank_total = sum(bank.values())
    by_subject = sorted(
        ({"name": name, "count": n, "share": round(100 * n / bank_total)} for name, n in bank.items()),
        key=lambda s: -s["count"])

    with_results = [e for e in exams if works.get(e.pk)]
    return {
        "bank": bank_total,
        "exams": len(exams),
        "works": len(all_works),
        "avg": _avg(all_works),
        "timeline": [
            {"label": e.title, "value": _avg(works[e.pk]), "date": e.opens_at,
             "url": reverse("exams:exam_results", args=[e.pk])}
            for e in with_results[-10:]],
        "by_subject": by_subject,
        "recent": [
            {"title": e.title, "subject": e.subject.name, "date": e.opens_at,
             "works": len(works.get(e.pk, [])), "avg": _avg(works.get(e.pk, [])),
             "url": reverse("exams:exam_results", args=[e.pk])}
            for e in reversed(exams[-5:])],
    }
