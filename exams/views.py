"""Веб-слой: принимает запрос, вызывает services и показывает результат."""
import csv
import json
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import services
from .forms import ExamForm, ImportForm, QuestionForm, StartExamForm, SubjectForm
from .importer import ImportError_, import_file, normalize_image
from .models import Attempt, Exam, Question, Subject, Violation
from .services import ExamError

PAGE_SIZE = 20


def teacher_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_teacher:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def client_ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR")


def session_key(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


# ---------- Главная ----------

@login_required
def dashboard(request):
    if request.user.is_teacher:
        exams = (Exam.objects.filter(author=request.user).select_related("subject")
                 .annotate(n_attempts=Count("attempts"),
                           n_active=Count("attempts", filter=Q(attempts__status="active"))))
        return render(request, "exams/teacher_dashboard.html", {
            "exams": exams[:8],
            "bank_size": Question.objects.filter(author=request.user, is_active=True).count(),
            "subjects": Subject.objects.annotate(
                n=Count("questions", filter=Q(questions__is_active=True))),
            "subject_form": SubjectForm(),
        })
    now = timezone.now()
    taken = Attempt.objects.filter(student=request.user).select_related("exam", "exam__subject")
    taken_ids = taken.values_list("exam_id", flat=True)
    available = [e for e in Exam.objects.filter(is_published=True, closes_at__gte=now)
                 .exclude(id__in=taken_ids).select_related("subject")
                 if not e.group_list or request.user.group.upper() in e.group_list]
    return render(request, "exams/student_dashboard.html",
                  {"available": available, "attempts": taken, "now": now})


@teacher_required
@require_POST
def subject_create(request):
    form = SubjectForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, f"Дисциплина «{form.instance.name}» добавлена")
    else:
        messages.error(request, "Такая дисциплина уже есть или название пустое")
    return redirect("dashboard")


# ---------- Банк вопросов ----------

@teacher_required
def question_list(request):
    params = request.GET
    qs = Question.objects.filter(is_active=True).select_related("subject", "author")
    if params.get("mine"):
        qs = qs.filter(author=request.user)
    qs = services.search_questions(
        qs, text=params.get("q", "").strip(), subject_id=params.get("subject") or None,
        topic=params.get("topic", "").strip(), difficulty=params.get("difficulty") or None,
        sort=params.get("sort", "new"),
    ).prefetch_related("choices")
    page = Paginator(qs, PAGE_SIZE).get_page(params.get("page"))
    topics = (Question.objects.filter(is_active=True).exclude(topic="")
              .values_list("topic", flat=True).distinct().order_by("topic"))
    query = params.copy()
    query.pop("page", None)
    return render(request, "exams/question_list.html", {
        "page": page, "subjects": Subject.objects.all(), "topics": topics,
        "sorts": services.QUESTION_SORTS.values(), "difficulties": Question.Difficulty.choices,
        "params": params, "query": query.urlencode(),
    })


@teacher_required
def question_edit(request, pk=None):
    question = get_object_or_404(Question, pk=pk) if pk else None
    form = QuestionForm(request.POST or None, request.FILES or None, question=question)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        image = d["image"].read() if d.get("image") else None
        if image:
            image, _ = normalize_image(image)
        try:
            saved, warnings = services.save_question(
                subject=d["subject"], author=request.user, text=d["text"],
                choices=form.choices_data(), topic=d["topic"], difficulty=d["difficulty"],
                image=image, question=question)
        except ExamError as exc:
            form.add_error(None, str(exc))
        else:
            for w in warnings:
                messages.warning(request, w)
            messages.success(request, "Вопрос сохранён")
            if "add_another" in request.POST:
                return redirect("exams:question_new")
            return redirect("exams:question_list")
    return render(request, "exams/question_form.html", {"form": form, "question": question})


@teacher_required
@require_POST
def question_delete(request, pk):
    question = get_object_or_404(Question, pk=pk)
    messages.info(request, services.delete_question(question))
    return redirect("exams:question_list")


@teacher_required
def question_import(request):
    form = ImportForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            items, errors = import_file(upload.name, upload.read())
        except ImportError_ as exc:
            form.add_error("file", str(exc))
        else:
            saved = 0
            if not form.cleaned_data["dry_run"]:
                saved, save_errors = services.save_imported(
                    items, subject=form.cleaned_data["subject"], author=request.user,
                    difficulty=form.cleaned_data["difficulty"])
                errors += save_errors
            report = {"items": items, "errors": errors, "saved": saved,
                      "dry_run": form.cleaned_data["dry_run"],
                      "with_warnings": sum(1 for i in items if i.warnings)}
    return render(request, "exams/question_import.html", {"form": form, "report": report})


# ---------- Тесты (преподаватель) ----------

@teacher_required
def exam_list(request):
    exams = (Exam.objects.filter(author=request.user).select_related("subject")
             .annotate(n_attempts=Count("attempts")))
    return render(request, "exams/exam_list.html", {"exams": exams, "now": timezone.now()})


@teacher_required
def exam_edit(request, pk=None):
    exam = get_object_or_404(Exam, pk=pk, author=request.user) if pk else None
    form = ExamForm(request.POST or None, instance=exam)
    if request.method == "POST" and form.is_valid():
        exam = form.save(commit=False)
        exam.author = request.user
        exam.save()
        pool = services.question_pool(exam).count()
        if pool < exam.question_count:
            messages.warning(request, f"В банке только {pool} подходящих вопросов, а в варианте "
                                      f"{exam.question_count}. Студенты не смогут начать тест")
        messages.success(request, "Тест сохранён")
        return redirect("exams:exam_results", pk=exam.pk)
    return render(request, "exams/exam_form.html", {"form": form, "exam": exam})


def _own_exam(request, pk) -> Exam:
    exam = get_object_or_404(Exam.objects.select_related("subject"), pk=pk)
    if exam.author_id != request.user.id and not request.user.is_superuser:
        raise PermissionDenied
    return exam


@teacher_required
def exam_results(request, pk):
    exam = _own_exam(request, pk)
    params = request.GET
    qs = services.search_results(
        exam.attempts.select_related("student"), text=params.get("q", "").strip(),
        group=params.get("group", ""), status=params.get("status", ""),
        flagged=bool(params.get("flagged")), sort=params.get("sort", "score_desc"))
    if params.get("export") == "csv":
        return _results_csv(exam, qs)
    groups = (exam.attempts.exclude(student__group="")
              .values_list("student__group", flat=True).distinct())
    report = services.exam_report(exam)
    peak = max(report["summary"]["buckets"]) or 1
    buckets = [{"label": f"{i * 10}–{i * 10 + 9 if i < 9 else 100}", "count": c,
                "height": round(100 * c / peak)} for i, c in enumerate(report["summary"]["buckets"])]
    return render(request, "exams/exam_results.html", {
        "exam": exam, "attempts": qs, "params": params, "groups": groups,
        "sorts": services.RESULT_SORTS.values(), "statuses": Attempt.Status.choices,
        "summary": report["summary"], "buckets": buckets,
        "pool": services.question_pool(exam).count(),
    })


def _results_csv(exam, qs):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="results_{exam.pk}.csv"'
    response.write("﻿")  # чтобы Excel открыл кириллицу
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["ФИО", "Группа", "Баллы", "Из", "%", "Статус", "Нарушения", "Начал"])
    for a in qs:
        writer.writerow([a.student.full_name, a.student.group, a.score, a.max_score, a.percent,
                         a.get_status_display(), a.violation_count,
                         timezone.localtime(a.started_at).strftime("%d.%m.%Y %H:%M")])
    return response


@teacher_required
def exam_analysis(request, pk):
    exam = _own_exam(request, pk)
    report = services.exam_report(exam)
    return render(request, "exams/exam_analysis.html", {"exam": exam, **report})


@teacher_required
def exam_monitor(request, pk):
    exam = _own_exam(request, pk)
    return render(request, "exams/exam_monitor.html", {"exam": exam})


@teacher_required
@never_cache
def exam_monitor_data(request, pk):
    exam = _own_exam(request, pk)
    now = timezone.now()
    rows = []
    attempts = (exam.attempts.select_related("student")
                .annotate(answered=Count("items", filter=Q(items__selected__isnull=False)))
                .order_by("-violation_count", "student__last_name"))
    for a in attempts:
        if a.status == Attempt.Status.ACTIVE and now > a.deadline:
            services.finish_attempt(a, status=Attempt.Status.EXPIRED, now=now)
        last = a.violations.first()
        rows.append({
            "id": a.id, "name": a.student.full_name, "group": a.student.group,
            "status": a.status, "status_label": a.get_status_display(),
            "answered": a.answered, "total": a.max_score, "violations": a.violation_count,
            "last_violation": last.get_kind_display() if last else "",
            "stale": services.is_stale(a, now), "left": services.seconds_left(a, now),
            "percent": a.percent, "ip": a.ip_address or "",
        })
    return JsonResponse({"rows": rows, "time": timezone.localtime(now).strftime("%H:%M:%S")})


@teacher_required
def attempt_detail(request, pk):
    attempt = get_object_or_404(Attempt.objects.select_related("exam", "student"), pk=pk)
    _own_exam(request, attempt.exam_id)
    return render(request, "exams/attempt_detail.html", {
        "attempt": attempt, "items": _review_items(attempt),
        "violations": attempt.violations.all(),
    })


@teacher_required
@require_POST
def attempt_manage(request, pk):
    """Действия преподавателя: разрешить продолжить или остановить попытку."""
    attempt = get_object_or_404(Attempt, pk=pk)
    _own_exam(request, attempt.exam_id)
    action = request.POST.get("action")
    try:
        if action == "resume":
            services.allow_resume(attempt)
            messages.success(request, f"{attempt.student.short_name} может войти заново "
                                      "и продолжить тест")
        elif action == "stop":
            services.finish_attempt(attempt, status=Attempt.Status.TERMINATED)
            messages.success(request, f"Попытка {attempt.student.short_name} остановлена")
    except ExamError as exc:
        messages.error(request, str(exc))
    return redirect("exams:attempt_detail", pk=pk)


def _review_items(attempt):
    items = attempt.items.select_related("question", "selected").prefetch_related(
        "question__choices")
    result = []
    for item in items:
        choices = {c.id: c for c in item.question.choices.all()}
        ordered = [choices[cid] for cid in item.choice_order if cid in choices]
        result.append({"item": item, "choices": ordered})
    return result


# ---------- Прохождение теста (студент) ----------

@login_required
def exam_start(request, pk):
    exam = get_object_or_404(Exam.objects.select_related("subject"), pk=pk)
    existing = Attempt.objects.filter(exam=exam, student=request.user).first()
    if existing:
        return redirect("exams:attempt_take", pk=existing.pk)
    form = StartExamForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.check_access(exam, request.user, code=form.cleaned_data["code"],
                                  ip=client_ip(request))
            attempt = services.start_attempt(exam, request.user, session_key=session_key(request),
                                             ip=client_ip(request))
        except ExamError as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("exams:attempt_take", pk=attempt.pk)
    return render(request, "exams/exam_start.html", {"exam": exam, "form": form})


def _student_attempt(request, pk) -> Attempt:
    attempt = get_object_or_404(Attempt.objects.select_related("exam"), pk=pk)
    if attempt.student_id != request.user.id:
        raise PermissionDenied
    return attempt


def _question_context(attempt, position):
    item = get_object_or_404(attempt.items.select_related("question"), position=position)
    choices = {c.id: c for c in item.question.choices.all()}
    return {
        "attempt": attempt, "item": item,
        "choices": [choices[cid] for cid in item.choice_order if cid in choices],
        "total": attempt.max_score,
    }


@login_required
@never_cache
def attempt_take(request, pk):
    attempt = _student_attempt(request, pk)
    try:
        services.ensure_active(attempt)
        services.ensure_same_session(attempt, session_key(request))
    except ExamError as exc:
        if attempt.status == Attempt.Status.ACTIVE:
            return render(request, "exams/attempt_blocked.html",
                          {"attempt": attempt, "reason": str(exc)}, status=409)
        return redirect("exams:attempt_result", pk=attempt.pk)
    ctx = _question_context(attempt, 1)
    ctx.update({
        "answered": list(attempt.items.filter(selected__isnull=False)
                         .values_list("position", flat=True)),
        "positions": range(1, attempt.max_score + 1),
        "seconds_left": services.seconds_left(attempt),
        "watermark": f"{request.user.short_name} · {request.user.masked_iin}",
    })
    return render(request, "exams/attempt_take.html", ctx)


@login_required
@never_cache
def attempt_question(request, pk, position):
    """HTML-фрагмент одного вопроса: страница не перезагружается,
    поэтому полноэкранный режим не сбрасывается."""
    attempt = _student_attempt(request, pk)
    try:
        services.ensure_active(attempt)
        services.ensure_same_session(attempt, session_key(request))
    except ExamError as exc:
        return JsonResponse({"error": str(exc), "status": attempt.status}, status=409)
    return render(request, "exams/_question.html", _question_context(attempt, position))


def _json_body(request) -> dict:
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return {}


@login_required
@require_POST
def attempt_answer(request, pk):
    attempt = _student_attempt(request, pk)
    data = _json_body(request)
    try:
        services.ensure_same_session(attempt, session_key(request))
        choice = data.get("choice")
        services.save_answer(attempt, int(data.get("position", 0)),
                             int(choice) if choice is not None else None)
    except (ExamError, TypeError, ValueError) as exc:
        return JsonResponse({"ok": False, "error": str(exc), "status": attempt.status},
                            status=409)
    return JsonResponse({"ok": True, "seconds_left": services.seconds_left(attempt)})


@login_required
@require_POST
def attempt_event(request, pk):
    attempt = _student_attempt(request, pk)
    kind = _json_body(request).get("kind", "")
    try:
        terminated = services.register_violation(attempt, kind)
    except ExamError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "terminated": terminated,
                         "violations": attempt.violation_count,
                         "limit": attempt.exam.max_violations})


@login_required
@require_POST
def attempt_ping(request, pk):
    attempt = _student_attempt(request, pk)
    try:
        services.ensure_active(attempt)
    except ExamError:
        return JsonResponse({"status": attempt.status, "seconds_left": 0})
    if attempt.session_key != session_key(request):
        return JsonResponse({"status": "other_session", "seconds_left": 0}, status=409)
    services.heartbeat(attempt)
    return JsonResponse({"status": attempt.status, "seconds_left": services.seconds_left(attempt),
                         "violations": attempt.violation_count})


@login_required
@require_POST
def attempt_finish(request, pk):
    attempt = _student_attempt(request, pk)
    if attempt.status == Attempt.Status.ACTIVE:
        status = (Attempt.Status.EXPIRED if services.seconds_left(attempt) == 0
                  else Attempt.Status.FINISHED)
        services.finish_attempt(attempt, status=status)
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"redirect": reverse("exams:attempt_result", args=[attempt.pk])})
    return redirect("exams:attempt_result", pk=attempt.pk)


@login_required
def attempt_result(request, pk):
    attempt = _student_attempt(request, pk)
    try:
        services.ensure_active(attempt)
    except ExamError:
        pass
    else:
        return redirect("exams:attempt_take", pk=attempt.pk)
    review = _review_items(attempt) if attempt.exam.show_review else None
    return render(request, "exams/attempt_result.html", {
        "attempt": attempt, "review": review,
        "violations": attempt.violations.all(),
        "violation_kinds": dict(Violation.Kind.choices),
    })
