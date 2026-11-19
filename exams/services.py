"""Бизнес-логика тестирования. Не зависит от HTTP: её можно вызвать
из веб-интерфейса, из консоли или из тестов (раздел 5.3 пособия)."""
import random
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Count, ExpressionWrapper, F, FloatField, Q, QuerySet
from django.db.models.functions import Cast
from django.utils import timezone

from .models import Attempt, AttemptItem, Choice, Exam, Question, Violation

GRACE_SECONDS = 5            # запас на сетевую задержку при последнем ответе
VIOLATION_DEBOUNCE_SECONDS = 3
HEARTBEAT_STALE_SECONDS = 45
MIN_CHOICES = 2


class ExamError(Exception):
    """Нарушено правило тестирования. Текст понятен пользователю."""


# ---------- Доступ к тесту ----------

def check_access(exam: Exam, student, *, code: str, ip: str | None, now=None) -> None:
    """Проверяет, может ли студент начать тест. Бросает ExamError."""
    now = now or timezone.now()
    if not exam.is_published:
        raise ExamError("Тест ещё не опубликован")
    if now < exam.opens_at:
        raise ExamError("Тест ещё не открыт")
    if now > exam.closes_at:
        raise ExamError("Время для прохождения теста закончилось")
    groups = exam.group_list
    if groups and (student.group or "").strip().upper() not in groups:
        raise ExamError("Этот тест назначен другой группе")
    if not secrets.compare_digest((code or "").strip().upper(), exam.access_code.strip().upper()):
        raise ExamError("Неверный код доступа. Его называет преподаватель в аудитории")
    prefix = exam.allowed_ip_prefix.strip()
    if prefix and not (ip or "").startswith(prefix):
        raise ExamError("Тест можно проходить только с компьютеров учебной аудитории")


def question_pool(exam: Exam) -> QuerySet:
    pool = Question.objects.filter(subject=exam.subject, is_active=True)
    if exam.topic:
        pool = pool.filter(topic__iexact=exam.topic)
    return pool.annotate(n=Count("choices")).filter(n__gte=MIN_CHOICES)


# ---------- Случайные варианты ----------

def build_variant(question_ids: list[int], choice_ids: dict[int, list[int]],
                  count: int, seed: int) -> list[tuple[int, list[int]]]:
    """Чистая функция: из банка выбирает `count` вопросов в случайном порядке
    и перемешивает варианты ответов. Одинаковый seed — одинаковый результат."""
    if count <= 0:
        raise ExamError("В варианте должен быть хотя бы один вопрос")
    if len(question_ids) < count:
        raise ExamError(f"В банке {len(question_ids)} подходящих вопросов, а нужно {count}")
    rnd = random.Random(seed)
    picked = rnd.sample(sorted(question_ids), count)
    variant = []
    for qid in picked:
        order = list(choice_ids[qid])
        rnd.shuffle(order)
        variant.append((qid, order))
    return variant


@transaction.atomic
def start_attempt(exam: Exam, student, *, session_key: str, ip: str | None,
                  now=None) -> Attempt:
    """Создаёт попытку с персональным вариантом. Повторно начать нельзя."""
    now = now or timezone.now()
    existing = Attempt.objects.filter(exam=exam, student=student).first()
    if existing:
        return existing
    pool = list(question_pool(exam).values_list("id", flat=True))
    choices: dict[int, list[int]] = {}
    for qid, cid in Choice.objects.filter(question_id__in=pool).values_list("question_id", "id"):
        choices.setdefault(qid, []).append(cid)
    seed = secrets.randbits(48)
    variant = build_variant(pool, choices, exam.question_count, seed)
    # Дедлайн не позже закрытия теста: опоздавший получает меньше времени.
    deadline = min(now + timedelta(minutes=exam.duration_minutes), exam.closes_at)
    try:
        attempt = Attempt.objects.create(
            exam=exam, student=student, started_at=now, deadline=deadline, seed=seed,
            session_key=session_key, ip_address=ip, last_seen_at=now, max_score=len(variant),
        )
    except IntegrityError as exc:
        raise ExamError("Попытка уже создана") from exc
    AttemptItem.objects.bulk_create(
        AttemptItem(attempt=attempt, question_id=qid, position=i, choice_order=order)
        for i, (qid, order) in enumerate(variant, start=1)
    )
    return attempt


# ---------- Ход тестирования ----------

def seconds_left(attempt: Attempt, now=None) -> int:
    now = now or timezone.now()
    return max(0, int((attempt.deadline - now).total_seconds()))


def ensure_active(attempt: Attempt, now=None) -> None:
    """Если время вышло — закрывает попытку. Бросает ExamError, если она не активна."""
    now = now or timezone.now()
    if attempt.status == Attempt.Status.ACTIVE and now > attempt.deadline + timedelta(
            seconds=GRACE_SECONDS):
        finish_attempt(attempt, status=Attempt.Status.EXPIRED, now=now)
    if attempt.status != Attempt.Status.ACTIVE:
        raise ExamError("Тест уже завершён")


def ensure_same_session(attempt: Attempt, session_key: str) -> None:
    """Тест идёт только в том браузере, где его начали."""
    if attempt.session_key and attempt.session_key != session_key:
        register_violation(attempt, Violation.Kind.OTHER_SESSION)
        raise ExamError("Тест уже открыт в другом браузере. Об этом сообщено преподавателю")


def save_answer(attempt: Attempt, position: int, choice_id: int | None, now=None) -> AttemptItem:
    now = now or timezone.now()
    ensure_active(attempt, now)
    try:
        item = attempt.items.get(position=position)
    except AttemptItem.DoesNotExist as exc:
        raise ExamError("Такого вопроса нет в вашем варианте") from exc
    if choice_id is not None and choice_id not in item.choice_order:
        raise ExamError("Такого варианта ответа нет у этого вопроса")
    item.selected_id = choice_id
    item.answered_at = now if choice_id else None
    item.save(update_fields=["selected", "answered_at"])
    return item


def score_items(items) -> int:
    return sum(1 for item in items if item.is_correct)


@transaction.atomic
def finish_attempt(attempt: Attempt, *, status=Attempt.Status.FINISHED, now=None) -> Attempt:
    if attempt.status != Attempt.Status.ACTIVE:
        return attempt
    items = list(attempt.items.select_related("selected"))
    attempt.score = score_items(items)
    attempt.max_score = len(items)
    attempt.status = status
    attempt.finished_at = now or timezone.now()
    attempt.save(update_fields=["score", "max_score", "status", "finished_at"])
    return attempt


def heartbeat(attempt: Attempt, now=None) -> None:
    Attempt.objects.filter(pk=attempt.pk).update(last_seen_at=now or timezone.now())


def register_violation(attempt: Attempt, kind: str, now=None) -> bool:
    """Записывает нарушение. Возвращает True, если попытка остановлена.
    Повторы в течение пары секунд (blur + visibilitychange) считаются одним."""
    now = now or timezone.now()
    if kind not in Violation.Kind.values:
        raise ExamError("Неизвестный тип нарушения")
    if attempt.status != Attempt.Status.ACTIVE:
        return attempt.status == Attempt.Status.TERMINATED
    recent = attempt.violations.filter(
        created_at__gte=now - timedelta(seconds=VIOLATION_DEBOUNCE_SECONDS)).exists()
    if not recent:
        Violation.objects.create(attempt=attempt, kind=kind, created_at=now)
        Attempt.objects.filter(pk=attempt.pk).update(violation_count=F("violation_count") + 1)
        attempt.refresh_from_db(fields=["violation_count"])
    limit = attempt.exam.max_violations
    if limit and attempt.violation_count >= limit:
        finish_attempt(attempt, status=Attempt.Status.TERMINATED, now=now)
        return True
    return False


def is_stale(attempt: Attempt, now=None) -> bool:
    """Страница теста давно не выходила на связь (закрыли, выключили сеть)."""
    now = now or timezone.now()
    return (attempt.status == Attempt.Status.ACTIVE and attempt.last_seen_at is not None
            and (now - attempt.last_seen_at).total_seconds() > HEARTBEAT_STALE_SECONDS)


# ---------- Поиск, фильтрация, сортировка ----------

@dataclass(frozen=True)
class SortOption:
    key: str
    label: str
    order_by: tuple[str, ...]


QUESTION_SORTS = {
    s.key: s for s in (
        SortOption("new", "Сначала новые", ("-created_at",)),
        SortOption("old", "Сначала старые", ("created_at",)),
        SortOption("hard", "Сначала сложные", ("-difficulty", "-created_at")),
        SortOption("easy", "Сначала лёгкие", ("difficulty", "-created_at")),
        SortOption("used", "Чаще всего в тестах", ("-times_used", "-created_at")),
        SortOption("weak", "Хуже всего решают", ("solve_rate", "-times_used")),
    )
}

RESULT_SORTS = {
    s.key: s for s in (
        SortOption("score_desc", "Сначала высокий балл", ("-score", "student__last_name")),
        SortOption("score_asc", "Сначала низкий балл", ("score", "student__last_name")),
        SortOption("name", "По фамилии", ("student__last_name", "student__first_name")),
        SortOption("violations", "Больше нарушений", ("-violation_count", "student__last_name")),
        SortOption("recent", "Последние начавшие", ("-started_at",)),
    )
}


def search_questions(qs: QuerySet, *, text="", subject_id=None, topic="", difficulty=None,
                     sort="new") -> QuerySet:
    """Поиск по тексту вопроса и вариантов, фильтры и сортировка."""
    qs = qs.annotate(
        times_used=Count("attempt_items", distinct=True),
        times_correct=Count("attempt_items", filter=Q(attempt_items__selected__is_correct=True),
                            distinct=True),
    ).annotate(
        solve_rate=ExpressionWrapper(
            Cast("times_correct", FloatField()) / (Cast("times_used", FloatField()) + 0.0001),
            output_field=FloatField()),
    )
    if text:
        qs = qs.filter(Q(text__icontains=text) | Q(choices__text__icontains=text)
                       | Q(topic__icontains=text)).distinct()
    if subject_id:
        qs = qs.filter(subject_id=subject_id)
    if topic:
        qs = qs.filter(topic__iexact=topic)
    if difficulty:
        qs = qs.filter(difficulty=difficulty)
    option = QUESTION_SORTS.get(sort, QUESTION_SORTS["new"])
    return qs.order_by(*option.order_by)


def search_results(qs: QuerySet, *, text="", group="", status="", flagged=False,
                   sort="score_desc") -> QuerySet:
    if text:
        qs = qs.filter(Q(student__last_name__icontains=text) | Q(student__first_name__icontains=text)
                       | Q(student__iin__startswith=text))
    if group:
        qs = qs.filter(student__group__iexact=group)
    if status:
        qs = qs.filter(status=status)
    if flagged:
        qs = qs.filter(violation_count__gt=0)
    option = RESULT_SORTS.get(sort, RESULT_SORTS["score_desc"])
    return qs.order_by(*option.order_by)
