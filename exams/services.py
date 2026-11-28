"""Бизнес-логика тестирования. Не зависит от HTTP: её можно вызвать
из веб-интерфейса, из консоли или из тестов (раздел 5.3 пособия)."""
from django.utils.translation import gettext_lazy as _
import random
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count, ExpressionWrapper, F, FloatField, Q, QuerySet
from django.db.models.functions import Cast
from django.utils import timezone

from .analysis import analyze_items, lint_question, score_summary
from .dbfuncs import ULower
from .importer import clean_text
from .models import Attempt, AttemptItem, Choice, Exam, Question, Retake, Subject, Violation

GRACE_SECONDS = 5            # запас на сетевую задержку при последнем ответе
VIOLATION_DEBOUNCE_SECONDS = 3
# Нарушением считается только уход со страницы теста. Правую кнопку, копирование
# и горячие клавиши браузер блокирует молча: их легко нажать случайно.
COUNTED_VIOLATIONS = {
    Violation.Kind.TAB_HIDDEN, Violation.Kind.WINDOW_BLUR, Violation.Kind.FULLSCREEN_EXIT,
    Violation.Kind.OTHER_SESSION, Violation.Kind.OFFLINE,
    Violation.Kind.AI_SITE, Violation.Kind.GUARD_LOST,
}
# Записываются и видны преподавателю, но не приближают завершение теста:
# расширение уже заблокировало действие, а само событие ещё не доказывает списывание.
NOTED_EVENTS = {
    Violation.Kind.NEW_TAB, Violation.Kind.AI_EXTENSION, Violation.Kind.GUARD_MISSING,
}
NOTE_DEBOUNCE_SECONDS = 10
HEARTBEAT_STALE_SECONDS = 45
MIN_CHOICES = 2


class ExamError(Exception):
    """Нарушено правило тестирования. Текст понятен пользователю."""


# ---------- Банк вопросов ----------

BLOCKING_PROBLEMS = (
    _("Пустой текст вопроса"),
    _("Нужно минимум два варианта ответа"),
    _("Должен быть ровно один правильный ответ"),
    _("Есть одинаковые варианты ответа"),
)


def validate_question(text: str, choices: list[tuple[str, bool]]) -> list[str]:
    """Возвращает предупреждения; бросает ExamError, если вопрос сохранить нельзя."""
    choices = [(t.strip(), ok) for t, ok in choices if t.strip()]
    problems = lint_question(text, choices)
    blocking = [p for p in problems if p in BLOCKING_PROBLEMS]
    if blocking:
        raise ExamError("; ".join(str(p) for p in blocking))
    return problems


@transaction.atomic
def save_question(*, subject, author, text: str, choices: list[tuple[str, bool]],
                  topic: str = "", difficulty: int = 2, image: bytes | None = None,
                  question: Question | None = None, exam: Exam | None = None) -> tuple[Question, list[str]]:
    """Создаёт или обновляет вопрос вместе с вариантами ответов.
    С `exam` вопрос сразу добавляется в этот тест (и остаётся в банке)."""
    text = clean_text(text)
    choices = [(clean_text(t), ok) for t, ok in choices if t and t.strip()]
    warnings = validate_question(text, choices)
    if question is None:
        question = Question(author=author)
    question.subject, question.text = subject, text
    question.topic, question.difficulty = topic.strip(), difficulty
    if image:
        question.image.save(f"q_{secrets.token_hex(6)}.png", ContentFile(image), save=False)
    question.save()
    used = question.attempt_items.exists()
    if used:
        # Варианты уже попадали студентам — меняем текст, но не ломаем историю ответов.
        existing = list(question.choices.all())
        if len(existing) != len(choices):
            raise ExamError(_("Вопрос уже был в тестах: число вариантов менять нельзя. Создайте новый вопрос"))
        for choice, (t, ok) in zip(existing, choices):
            choice.text, choice.is_correct = t, ok
            choice.save()
    else:
        question.choices.all().delete()
        Choice.objects.bulk_create(Choice(question=question, text=t, is_correct=ok)
                                   for t, ok in choices)
    if exam is not None:
        exam.questions.add(question)
    return question, warnings


def save_imported(items, *, subject, author, difficulty: int = 2,
                  exam: Exam | None = None) -> tuple[int, list[str]]:
    """Сохраняет вопросы из импорта; возвращает число сохранённых и ошибки."""
    saved, errors = 0, []
    for number, item in enumerate(items, start=1):
        try:
            save_question(subject=subject, author=author, text=item.text,
                          choices=item.choices, topic=item.topic, difficulty=difficulty,
                          image=item.image, exam=exam)
            saved += 1
        except ExamError as exc:
            errors.append(_("Вопрос %(n)s: %(error)s") % {"n": number, "error": exc})
    return saved, errors


def delete_question(question: Question) -> str:
    """Неиспользованный вопрос удаляется, использованный — убирается из банка."""
    if question.attempt_items.exists():
        question.is_active = False
        question.save(update_fields=["is_active"])
        return _("Вопрос уже был в тестах, поэтому он скрыт из банка, а статистика сохранена")
    question.delete()
    return _("Вопрос удалён")


def add_from_bank(exam: Exam, question_ids) -> int:
    """Добавляет в тест вопросы банка той же дисциплины. Возвращает, сколько добавлено."""
    ids = set(Question.objects.filter(id__in=question_ids, subject=exam.subject, is_active=True)
              .exclude(exams=exam).values_list("id", flat=True))
    exam.questions.add(*ids)
    return len(ids)


def remove_from_exam(exam: Exam, question: Question) -> None:
    """Убирает вопрос из теста. Сам вопрос остаётся в банке, а уже выданные
    варианты не меняются."""
    exam.questions.remove(question)


def set_published(exam: Exam, published: bool) -> None:
    if published:
        have = question_pool(exam).count()
        if have < exam.question_count:
            raise ExamError(_("Вопросов в тесте: %(have)s, а в варианте должно быть %(need)s. Добавьте вопросы, потом публикуйте")
                            % {"have": have, "need": exam.question_count})
    exam.is_published = published
    exam.save(update_fields=["is_published"])


def delete_exam(exam: Exam) -> str:
    if exam.attempts.filter(status=Attempt.Status.ACTIVE).exists():
        raise ExamError(_("Тест сейчас пишут. Удалить его можно, когда все закончат"))
    title = exam.title
    exam.delete()
    return _("Тест «%(title)s» удалён вместе с результатами") % {"title": title}


def can_edit_question(user, question: Question) -> bool:
    """Банк общий: брать чужие вопросы в свой тест можно, менять и удалять нельзя."""
    return user.is_admin or question.author_id == user.id


def delete_subject(subject: Subject, user=None) -> str:
    """Дисциплину можно удалить, пока по ней нет тестов. Её вопросы удаляются вместе с ней.
    Если в ней есть вопросы других преподавателей, удалить её может только администратор."""
    if user is not None and not user.is_admin and subject.questions.exclude(author=user).exists():
        raise ExamError(_("В дисциплине «%(name)s» есть вопросы других преподавателей. Удалить её может администратор")
                        % {"name": subject.name})
    n_exams = subject.exams.count()
    if n_exams:
        raise ExamError(_("По дисциплине «%(name)s» есть тесты (%(n)s). Сначала удалите их")
                        % {"name": subject.name, "n": n_exams})
    if AttemptItem.objects.filter(question__subject=subject).exists():
        raise ExamError(_("Вопросы этой дисциплины уже попадали студентам, поэтому её нельзя удалить"))
    name = subject.name
    subject.delete()
    return _("Дисциплина «%(name)s» удалена") % {"name": name}


# ---------- Пересдача ----------

def active_retake(exam: Exam, student, now=None) -> Retake | None:
    now = now or timezone.now()
    return (Retake.objects.filter(exam=exam, student=student, used_at__isnull=True, expires_at__gte=now)
            .order_by("-expires_at").first())


def grant_retake(exam: Exam, student, *, granted_by, expires_at, now=None) -> Retake:
    """Администратор разрешает студенту пройти тест заново до `expires_at`,
    даже если тест уже закрыт. Прошлая попытка останется в истории."""
    now = now or timezone.now()
    if expires_at <= now:
        raise ExamError(_("Срок пересдачи должен быть в будущем"))
    if Attempt.objects.filter(exam=exam, student=student, status=Attempt.Status.ACTIVE).exists():
        raise ExamError(_("Студент сейчас пишет этот тест"))
    Retake.objects.filter(exam=exam, student=student, used_at__isnull=True).delete()
    return Retake.objects.create(exam=exam, student=student, granted_by=granted_by,
                                 created_at=now, expires_at=expires_at)


def cancel_retake(exam: Exam, student) -> None:
    Retake.objects.filter(exam=exam, student=student, used_at__isnull=True).delete()


# ---------- Доступ к тесту ----------

def check_access(exam: Exam, student, *, code: str, ip: str | None, now=None) -> None:
    """Проверяет, может ли студент начать тест. Бросает ExamError."""
    now = now or timezone.now()
    if not exam.is_published:
        raise ExamError(_("Тест ещё не опубликован"))
    # Пересдачу открыл администратор лично для этого студента: сроки и группа не важны.
    if active_retake(exam, student, now) is None:
        if now < exam.opens_at:
            raise ExamError(_("Тест ещё не открыт"))
        if now > exam.closes_at:
            raise ExamError(_("Время для прохождения теста закончилось"))
        groups = exam.group_list
        if groups and (student.group or "").strip().upper() not in groups:
            raise ExamError(_("Этот тест назначен другой группе"))
    if not secrets.compare_digest((code or "").strip().upper(), exam.access_code.strip().upper()):
        raise ExamError(_("Неверный код доступа. Его называет преподаватель в аудитории"))
    prefix = exam.allowed_ip_prefix.strip()
    if prefix and not (ip or "").startswith(prefix):
        raise ExamError(_("Тест можно проходить только с компьютеров учебной аудитории"))


def question_pool(exam: Exam) -> QuerySet:
    """Вопросы, из которых собираются варианты: те, что добавлены в тест."""
    return (exam.questions.filter(is_active=True)
            .annotate(n=Count("choices")).filter(n__gte=MIN_CHOICES))


# ---------- Случайные варианты ----------

def build_variant(question_ids: list[int], choice_ids: dict[int, list[int]],
                  count: int, seed: int) -> list[tuple[int, list[int]]]:
    """Чистая функция: из банка выбирает `count` вопросов в случайном порядке
    и перемешивает варианты ответов. Одинаковый seed — одинаковый результат."""
    if count <= 0:
        raise ExamError(_("В варианте должен быть хотя бы один вопрос"))
    if len(question_ids) < count:
        raise ExamError(_("Вопросов в тесте: %(have)s, а в варианте нужно %(need)s") % {"have": len(question_ids), "need": count})
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
    retake = active_retake(exam, student, now)
    existing = Attempt.objects.filter(exam=exam, student=student).first()
    if existing:
        if retake is None or existing.status == Attempt.Status.ACTIVE:
            return existing
        # Пересдача: прошлая попытка уходит в историю, студент пишет заново.
        existing.archived = True
        existing.save(update_fields=["archived"])
    pool = list(question_pool(exam).values_list("id", flat=True))
    choices: dict[int, list[int]] = {}
    for qid, cid in Choice.objects.filter(question_id__in=pool).values_list("question_id", "id"):
        choices.setdefault(qid, []).append(cid)
    seed = secrets.randbits(48)
    variant = build_variant(pool, choices, exam.question_count, seed)
    # Дедлайн не позже закрытия теста: опоздавший получает меньше времени.
    # При пересдаче тест для студента открыт до срока, который дал администратор.
    closes_at = max(exam.closes_at, retake.expires_at) if retake else exam.closes_at
    deadline = min(now + timedelta(minutes=exam.duration_minutes), closes_at)
    try:
        attempt = Attempt.objects.create(
            exam=exam, student=student, started_at=now, deadline=deadline, seed=seed,
            session_key=session_key, ip_address=ip, last_seen_at=now, max_score=len(variant),
        )
    except IntegrityError as exc:
        raise ExamError(_("Попытка уже создана")) from exc
    AttemptItem.objects.bulk_create(
        AttemptItem(attempt=attempt, question_id=qid, position=i, choice_order=order)
        for i, (qid, order) in enumerate(variant, start=1)
    )
    if retake:
        Retake.objects.filter(pk=retake.pk).update(used_at=now)
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
        raise ExamError(_("Тест уже завершён"))


def ensure_same_session(attempt: Attempt, session_key: str) -> None:
    """Тест идёт только в том браузере, где его начали.
    Если преподаватель разрешил продолжить (ключ сброшен) — браузер закрепляется заново."""
    if not attempt.session_key:
        attempt.session_key = session_key
        attempt.save(update_fields=["session_key"])
        return
    if attempt.session_key != session_key:
        register_violation(attempt, Violation.Kind.OTHER_SESSION)
        raise ExamError(_("Тест уже открыт в другом браузере. Об этом сообщено преподавателю"))


def save_answer(attempt: Attempt, position: int, choice_id: int | None, now=None) -> AttemptItem:
    now = now or timezone.now()
    ensure_active(attempt, now)
    try:
        item = attempt.items.get(position=position)
    except AttemptItem.DoesNotExist as exc:
        raise ExamError(_("Такого вопроса нет в вашем варианте")) from exc
    if choice_id is not None and choice_id not in item.choice_order:
        raise ExamError(_("Такого варианта ответа нет у этого вопроса"))
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


def expire_overdue(attempts=None, now=None) -> int:
    """Закрывает попытки, у которых вышло время, даже если студент закрыл страницу
    и никто не смотрел «Наблюдение». Вызывается перед показом результатов и статистики."""
    now = now or timezone.now()
    qs = (attempts if attempts is not None else Attempt.objects.all()).filter(
        status=Attempt.Status.ACTIVE, deadline__lt=now - timedelta(seconds=GRACE_SECONDS))
    closed = 0
    for attempt in qs.select_related(None):
        finish_attempt(attempt, status=Attempt.Status.EXPIRED, now=attempt.deadline)
        closed += 1
    return closed


def heartbeat(attempt: Attempt, now=None) -> None:
    Attempt.objects.filter(pk=attempt.pk).update(last_seen_at=now or timezone.now())


def register_violation(attempt: Attempt, kind: str, now=None, detail: str = "") -> bool:
    """Записывает нарушение. Возвращает True, если попытка остановлена.
    Повторы в течение пары секунд (blur + visibilitychange) считаются одним."""
    now = now or timezone.now()
    if kind not in Violation.Kind.values:
        raise ExamError(_("Неизвестный тип нарушения"))
    if attempt.status != Attempt.Status.ACTIVE:
        return attempt.status == Attempt.Status.TERMINATED
    detail = " ".join(str(detail or "").split())[:200]
    if kind in NOTED_EVENTS:
        repeated = attempt.violations.filter(
            kind=kind, detail=detail,
            created_at__gte=now - timedelta(seconds=NOTE_DEBOUNCE_SECONDS)).exists()
        if not repeated:
            Violation.objects.create(attempt=attempt, kind=kind, detail=detail, counted=False,
                                     created_at=now)
        return False
    if kind not in COUNTED_VIOLATIONS:
        return False
    recent = attempt.violations.filter(
        counted=True, created_at__gte=now - timedelta(seconds=VIOLATION_DEBOUNCE_SECONDS)).exists()
    if not recent:
        Violation.objects.create(attempt=attempt, kind=kind, detail=detail, created_at=now)
        Attempt.objects.filter(pk=attempt.pk).update(violation_count=F("violation_count") + 1)
        attempt.refresh_from_db(fields=["violation_count"])
    limit = attempt.exam.max_violations
    if limit and attempt.violation_count >= limit:
        finish_attempt(attempt, status=Attempt.Status.TERMINATED, now=now)
        return True
    return False


def allow_resume(attempt: Attempt) -> None:
    """Преподаватель разрешает продолжить тест в другом браузере (например, после сбоя ПК)."""
    if attempt.status != Attempt.Status.ACTIVE:
        raise ExamError(_("Попытка уже завершена"))
    attempt.session_key = ""
    attempt.save(update_fields=["session_key"])


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
        SortOption("new", _("Сначала новые"), ("-created_at",)),
        SortOption("old", _("Сначала старые"), ("created_at",)),
        SortOption("hard", _("Сначала сложные"), ("-difficulty", "-created_at")),
        SortOption("easy", _("Сначала лёгкие"), ("difficulty", "-created_at")),
        SortOption("used", _("Чаще всего в тестах"), ("-times_used", "-created_at")),
        SortOption("weak", _("Хуже всего решают"), ("solve_rate", "-times_used")),
    )
}

RESULT_SORTS = {
    s.key: s for s in (
        SortOption("score_desc", _("Сначала высокий балл"), ("-score", "student__last_name")),
        SortOption("score_asc", _("Сначала низкий балл"), ("score", "student__last_name")),
        SortOption("name", _("По фамилии"), ("student__last_name", "student__first_name")),
        SortOption("violations", _("Больше нарушений"), ("-violation_count", "student__last_name")),
        SortOption("recent", _("Последние начавшие"), ("-started_at",)),
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
        needle = text.lower()
        in_choices = (Choice.objects.annotate(t=ULower("text")).filter(t__contains=needle)
                      .values("question_id"))
        qs = qs.annotate(text_l=ULower("text"), topic_l=ULower("topic")).filter(
            Q(text_l__contains=needle) | Q(topic_l__contains=needle) | Q(id__in=in_choices))
    if subject_id:
        qs = qs.filter(subject_id=subject_id)
    if topic:
        qs = qs.annotate(topic_eq=ULower("topic")).filter(topic_eq=topic.lower())
    if difficulty:
        qs = qs.filter(difficulty=difficulty)
    option = QUESTION_SORTS.get(sort, QUESTION_SORTS["new"])
    return qs.order_by(*option.order_by)


def search_results(qs: QuerySet, *, text="", group="", status="", flagged=False,
                   sort="score_desc") -> QuerySet:
    if text:
        needle = text.lower()
        qs = qs.annotate(last_l=ULower("student__last_name"), first_l=ULower("student__first_name"))
        qs = qs.filter(Q(last_l__contains=needle) | Q(first_l__contains=needle)
                       | Q(student__iin__startswith=text))
    if group:
        qs = qs.annotate(group_l=ULower("student__group")).filter(group_l=group.lower())
    if status:
        qs = qs.filter(status=status)
    if flagged:
        qs = qs.filter(violation_count__gt=0)
    option = RESULT_SORTS.get(sort, RESULT_SORTS["score_desc"])
    return qs.order_by(*option.order_by)


# ---------- Анализ результатов теста ----------

FINISHED_STATUSES = (Attempt.Status.FINISHED, Attempt.Status.EXPIRED, Attempt.Status.TERMINATED)


def exam_report(exam: Exam) -> dict:
    expire_overdue(exam.attempts.all())
    """Сводка по тесту: распределение баллов и анализ каждого вопроса."""
    attempts = list(exam.attempts.filter(status__in=FINISHED_STATUSES))
    items = AttemptItem.objects.filter(attempt__in=attempts).values_list(
        "attempt_id", "question_id", "selected_id")
    by_attempt: dict[int, dict[int, int | None]] = {a.id: {} for a in attempts}
    for attempt_id, qid, cid in items:
        by_attempt[attempt_id][qid] = cid
    answers = [(a.percent, by_attempt[a.id]) for a in attempts]
    qids = {qid for chosen in by_attempt.values() for qid in chosen}
    questions = {
        q.id: {"text": q.text,
               "choices": [(c.id, c.text, c.is_correct) for c in q.choices.all()]}
        for q in Question.objects.filter(id__in=qids).prefetch_related("choices")
    }
    stats = analyze_items(questions, answers)
    stats.sort(key=lambda s: (len(s.flags) == 0, s.p))
    return {"summary": score_summary([a.percent for a in attempts]), "items": stats}
