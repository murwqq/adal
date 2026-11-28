"""Веб-слой: принимает запрос, вызывает services и показывает результат."""
from django.utils.translation import gettext_lazy as _
import csv
import json
import secrets
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
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import ai, services
from .forms import ExamForm, ImportForm, QuestionForm, StartExamForm, SubjectForm, SyllabusForm
from .importer import ImportError_, import_file, normalize_image
from .models import Attempt, AttemptItem, Exam, Question, Retake, Subject, TutorSession, Violation
from .services import ExamError
from .syllabus import SyllabusError, extract_text

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


BROWSER_ID = "browser_id"


def session_key(request) -> str:
    """Постоянный идентификатор браузера в рамках сессии.
    Не используем сам ключ сессии: при хранении сессии в cookie он меняется."""
    if BROWSER_ID not in request.session:
        request.session[BROWSER_ID] = secrets.token_hex(16)
    return request.session[BROWSER_ID]


# ---------- Главная ----------

@login_required
def dashboard(request):
    if request.user.is_teacher:
        services.expire_overdue(Attempt.objects.filter(exam__author=request.user))
        exams = (Exam.objects.filter(author=request.user).select_related("subject")
                 .annotate(n_attempts=Count("attempts", filter=Q(attempts__archived=False)),
                           n_active=Count("attempts", filter=Q(attempts__status="active",
                                                               attempts__archived=False))))
        return render(request, "exams/teacher_dashboard.html", {
            "exams": exams[:8],
            "bank_size": Question.objects.filter(author=request.user, is_active=True).count(),
            "subjects": Subject.objects.annotate(
                n=Count("questions", filter=Q(questions__is_active=True), distinct=True),
                n_foreign=Count("questions", filter=~Q(questions__author=request.user), distinct=True)),
            "subject_form": SubjectForm(),
        })
    now = timezone.now()
    services.expire_overdue(Attempt.objects.filter(student=request.user), now)
    taken = Attempt.objects.filter(student=request.user).select_related("exam", "exam__subject")
    taken_ids = set(taken.values_list("exam_id", flat=True))
    retakes = {r.exam_id: r for r in Retake.objects.filter(
        student=request.user, used_at__isnull=True, expires_at__gte=now)}
    available = [e for e in Exam.objects.filter(is_published=True, closes_at__gte=now)
                 .exclude(id__in=taken_ids | set(retakes)).select_related("subject")
                 if (not e.group_list or request.user.group.upper() in e.group_list)
                 and services.question_pool(e).count() >= e.question_count]
    for exam in Exam.objects.filter(id__in=retakes, is_published=True).select_related("subject"):
        exam.retake_until = retakes[exam.id].expires_at
        available.insert(0, exam)
    return render(request, "exams/student_dashboard.html",
                  {"available": available, "attempts": taken, "now": now})


@teacher_required
@require_POST
def subject_create(request):
    form = SubjectForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, _("Дисциплина «%(name)s» добавлена") % {"name": form.instance.name})
    else:
        for error in form.errors.get("name", [_("Такая дисциплина уже есть или название пустое")]):
            messages.error(request, error)
    return redirect("dashboard")


@teacher_required
@require_POST
def subject_delete(request, pk):
    subject = get_object_or_404(Subject, pk=pk)
    try:
        messages.success(request, services.delete_subject(subject, request.user))
    except ExamError as exc:
        messages.error(request, str(exc))
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


def _exam_from_request(request):
    """Тест, внутри которого пишутся или загружаются вопросы (?exam=…)."""
    exam_id = request.POST.get("exam") or request.GET.get("exam")
    return _own_exam(request, exam_id) if exam_id and str(exam_id).isdigit() else None


@teacher_required
def question_edit(request, pk=None):
    question = get_object_or_404(Question, pk=pk) if pk else None
    if question is not None and not services.can_edit_question(request.user, question):
        raise PermissionDenied
    exam = _exam_from_request(request)
    form = QuestionForm(request.POST or None, request.FILES or None, question=question, exam=exam,
                        keep=request.GET if question is None else None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        image = d["image"].read() if d.get("image") else None
        if image:
            image, _image_warnings = normalize_image(image)
        try:
            saved, warnings = services.save_question(
                subject=d["subject"], author=request.user, text=d["text"],
                choices=form.choices_data(), topic=d["topic"], difficulty=d["difficulty"],
                image=image, question=question, exam=exam)
        except ExamError as exc:
            form.add_error(None, str(exc))
        else:
            for w in warnings:
                messages.warning(request, w)
            messages.success(request, _("Вопрос сохранён"))
            if "add_another" in request.POST:
                from urllib.parse import urlencode
                keep = {"topic": d["topic"], "difficulty": d["difficulty"]}
                if exam:
                    keep["exam"] = exam.pk
                else:
                    keep["subject"] = d["subject"].pk
                return redirect(f"{reverse('exams:question_new')}?{urlencode(keep)}")
            if exam:
                return redirect("exams:exam_questions", pk=exam.pk)
            return redirect("exams:question_list")
    return render(request, "exams/question_form.html",
                  {"form": form, "question": question, "exam": exam})


@teacher_required
@require_POST
def question_delete(request, pk):
    question = get_object_or_404(Question, pk=pk)
    if not services.can_edit_question(request.user, question):
        raise PermissionDenied
    messages.info(request, services.delete_question(question))
    return redirect("exams:question_list")


@teacher_required
def question_import(request):
    exam = _exam_from_request(request)
    form = ImportForm(request.POST or None, request.FILES or None, exam=exam)
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
                    difficulty=form.cleaned_data["difficulty"], exam=exam)
                errors += save_errors
            report = {"items": items, "errors": errors, "saved": saved,
                      "dry_run": form.cleaned_data["dry_run"],
                      "with_warnings": sum(1 for i in items if i.warnings)}
    return render(request, "exams/question_import.html",
                  {"form": form, "report": report, "exam": exam})


@teacher_required
def question_generate(request):
    """ИИ предлагает черновики вопросов по силлабусу. В банк попадает только то,
    что преподаватель проверил и отметил."""
    exam = _exam_from_request(request)
    if request.method == "POST" and request.POST.get("action") == "save":
        return _save_generated(request, exam)
    form = SyllabusForm(request.POST or None, request.FILES or None, exam=exam,
                        initial={"language": (request.LANGUAGE_CODE or "ru")[:2]})
    drafts, model = None, ""
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            text = extract_text(upload.name, upload.read())
            drafts, model = ai.generate_questions(
                syllabus=text, count=form.cleaned_data["count"], focus=form.cleaned_data["focus"],
                lang=form.cleaned_data["language"], request=request)
        except SyllabusError as exc:
            form.add_error("file", str(exc))
        except ai.AIUnavailable:
            form.add_error(None, _("ИИ не смог подготовить вопросы. Попробуйте ещё раз или уменьшите число вопросов"))
        else:
            existing = {" ".join(t.lower().split()) for t in
                        Question.objects.filter(subject=form.cleaned_data["subject"]).values_list("text", flat=True)}
            for d in drafts:
                d["letters"] = list(zip("АБВГ", d["options"]))
                d["warnings"] = [str(w) for w in services.lint_question(
                    d["text"], [(o, i == d["correct"]) for i, o in enumerate(d["options"])])]
                d["duplicate"] = " ".join(d["text"].lower().split()) in existing
    return render(request, "exams/question_generate.html", {
        "form": form, "exam": exam, "drafts": drafts, "model": model,
        "difficulties": Question.Difficulty.choices})


def _save_generated(request, exam):
    subject = get_object_or_404(Subject, pk=request.POST.get("subject"))
    if exam is not None and exam.subject_id != subject.pk:
        raise PermissionDenied
    saved, problems = 0, []
    total = min(int(request.POST.get("total", 0) or 0), 20)
    for i in range(total):
        if not request.POST.get(f"q{i}-keep"):
            continue
        text = request.POST.get(f"q{i}-text", "")
        options = [request.POST.get(f"q{i}-o{j}", "") for j in range(4)]
        correct = request.POST.get(f"q{i}-correct", "")
        difficulty = request.POST.get(f"q{i}-difficulty", "2")
        try:
            services.save_question(
                subject=subject, author=request.user, text=text,
                choices=[(o, str(j) == correct) for j, o in enumerate(options)],
                topic=request.POST.get(f"q{i}-topic", "")[:150],
                difficulty=int(difficulty) if difficulty in ("1", "2", "3") else 2, exam=exam)
            saved += 1
        except ExamError as exc:
            problems.append(_("Вопрос %(n)s не сохранён: %(error)s") % {"n": i + 1, "error": exc})
    for p in problems:
        messages.warning(request, p)
    if saved:
        messages.success(request, _("Сохранено вопросов: %(n)s") % {"n": saved})
    if exam is not None:
        return redirect("exams:exam_questions", pk=exam.pk)
    return redirect("exams:question_list")


def guard(request):
    """Страница расширения Адал Guard: что делает, что не делает, как установить."""
    from django.conf import settings
    try:
        manifest = settings.BASE_DIR / "extension" / "adal-guard" / "manifest.json"
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        version = "1.0.0"
    return render(request, "exams/guard.html", {"version": version})


# ---------- Тесты (преподаватель) ----------

@teacher_required
def exam_list(request):
    exams = (Exam.objects.filter(author=request.user).select_related("subject")
             .annotate(n_attempts=Count("attempts", filter=Q(attempts__archived=False), distinct=True),
                       n_questions=Count("questions", distinct=True)))
    services.expire_overdue(Attempt.objects.filter(exam__author=request.user))
    return render(request, "exams/exam_list.html", {"exams": exams, "now": timezone.now()})


@teacher_required
def exam_edit(request, pk=None):
    exam = get_object_or_404(Exam, pk=pk, author=request.user) if pk else None
    form = ExamForm(request.POST or None, instance=exam,
                    initial={"source": request.GET.get("source", "manual")})
    if request.method == "POST" and form.is_valid():
        is_new = exam is None
        exam = form.save(commit=False)
        exam.author = request.user
        exam.save()
        if is_new:
            messages.success(request, _("Тест создан. Теперь добавьте в него вопросы"))
            if form.cleaned_data.get("source") == "word":
                return redirect(f"{reverse('exams:question_import')}?exam={exam.pk}")
            return redirect("exams:exam_questions", pk=exam.pk)
        pool = services.question_pool(exam).count()
        if pool < exam.question_count:
            messages.warning(request, _("Вопросов в тесте: %(pool)s, а в варианте %(count)s. Студенты не смогут начать тест") % {"pool": pool, "count": exam.question_count})
        messages.success(request, _("Тест сохранён"))
        return redirect("exams:exam_results", pk=exam.pk)
    return render(request, "exams/exam_form.html", {"form": form, "exam": exam})


@teacher_required
def exam_questions(request, pk):
    """Вопросы теста: написать новый, загрузить из Word, взять из банка, убрать."""
    exam = _own_exam(request, pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove":
            question = get_object_or_404(Question, pk=request.POST.get("question"))
            services.remove_from_exam(exam, question)
            messages.info(request, _("Вопрос убран из теста. В банке он остался"))
        elif action in ("publish", "unpublish"):
            try:
                services.set_published(exam, action == "publish")
            except ExamError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Тест опубликован, студенты его видят") if action == "publish"
                                 else _("Тест снят с публикации, студенты его не видят"))
        elif action == "add_bank":
            added = services.add_from_bank(exam, request.POST.getlist("questions"))
            messages.success(request, _("Добавлено вопросов из банка: %(n)s") % {"n": added})
        return redirect("exams:exam_questions", pk=exam.pk)
    questions = (exam.questions.filter(is_active=True).select_related("author")
                 .prefetch_related("choices").order_by("pk"))
    for q in questions:
        q.editable = services.can_edit_question(request.user, q)
    bank_query = request.GET.get("q", "").strip()
    bank = services.search_questions(
        Question.objects.filter(subject=exam.subject, is_active=True).exclude(exams=exam),
        text=bank_query, sort="new").prefetch_related("choices")
    return render(request, "exams/exam_questions.html", {
        "exam": exam, "questions": questions, "bank": bank[:50], "bank_total": bank.count(),
        "bank_query": bank_query, "open_bank": bool(bank_query) or request.GET.get("bank") == "1",
    })


@teacher_required
@require_POST
def exam_delete(request, pk):
    exam = _own_exam(request, pk)
    try:
        messages.success(request, services.delete_exam(exam))
    except ExamError as exc:
        messages.error(request, str(exc))
        return redirect("exams:exam_results", pk=pk)
    return redirect("exams:exam_list")


def _own_exam(request, pk) -> Exam:
    exam = get_object_or_404(Exam.objects.select_related("subject"), pk=pk)
    if exam.author_id != request.user.id and not request.user.is_admin:
        raise PermissionDenied
    return exam


@teacher_required
def exam_results(request, pk):
    exam = _own_exam(request, pk)
    services.expire_overdue(exam.attempts.all())
    params = request.GET
    qs = services.search_results(
        exam.attempts.select_related("student"), text=params.get("q", "").strip(),
        group=params.get("group", ""), status=params.get("status", ""),
        flagged=bool(params.get("flagged")), sort=params.get("sort", "score_desc"))
    if params.get("export") == "csv":
        return _results_csv(exam, qs)
    groups = (exam.attempts.exclude(student__group="").order_by("student__group")
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
    writer.writerow([_("ФИО"), _("Группа"), _("Баллы"), _("Из"), "%", _("Статус"), _("Нарушения"), _("Начал")])
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
@require_POST
def exam_question_ai(request, pk, qid):
    """ИИ объясняет преподавателю, что не так с вопросом, по тексту и статистике ответов."""
    exam = _own_exam(request, pk)
    stat = next((s for s in services.exam_report(exam)["items"] if s.question_id == qid), None)
    if stat is None:
        return JsonResponse({"error": str(_("По этому вопросу ещё нет ответов."))}, status=404)
    try:
        text, model = ai.review_question(
            text=stat.text, choices=[(c.text, c.is_correct, c.share) for c in stat.choices],
            p=stat.p, d=stat.d, answered=stat.answered, flags=stat.flags, request=request)
    except ai.AIUnavailable as exc:
        # Преподавателю показываем техническую причину: без неё не понять, что чинить.
        return JsonResponse({"error": f"{_('ИИ сейчас недоступен. Попробуйте через минуту.')} ({exc})"}, status=503)
    return JsonResponse({"text": text, "model": model})


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
            "last_violation": (f"{last.get_kind_display()}: {last.detail}" if last and last.detail
                               else last.get_kind_display() if last else ""),
            "notes": a.violations.filter(counted=False).count(),
            "guard": a.guard_version,
            "stale": services.is_stale(a, now), "left": services.seconds_left(a, now),
            "percent": a.percent, "ip": a.ip_address or "",
        })
    return JsonResponse({"rows": rows, "time": timezone.localtime(now).strftime("%H:%M:%S")})


@teacher_required
def attempt_detail(request, pk):
    attempt = get_object_or_404(Attempt.all_objects.select_related("exam", "student"), pk=pk)
    _own_exam(request, attempt.exam_id)
    history = (Attempt.all_objects.filter(exam=attempt.exam, student=attempt.student)
               .exclude(pk=attempt.pk).order_by("-started_at"))
    default_until = timezone.localtime() + timezone.timedelta(days=1)
    sessions = {t.item_id: t for t in TutorSession.objects.filter(item__attempt=attempt)}
    items = _review_items(attempt)
    for row in items:
        row["tutor"] = sessions.get(row["item"].id)
    return render(request, "exams/attempt_detail.html", {
        "attempt": attempt, "items": items,
        "violations": attempt.violations.all(), "history": history,
        "retake": services.active_retake(attempt.exam, attempt.student),
        "retake_default": default_until.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M"),
    })


@teacher_required
@require_POST
def attempt_manage(request, pk):
    """Действия преподавателя: разрешить продолжить или остановить попытку."""
    attempt = get_object_or_404(Attempt.all_objects, pk=pk)
    _own_exam(request, attempt.exam_id)
    action = request.POST.get("action")
    try:
        if action in ("retake", "cancel_retake") and not request.user.is_admin:
            raise PermissionDenied
        if action == "retake":
            raw = parse_datetime(request.POST.get("until", ""))
            if raw is None:
                raise ExamError(_("Укажите, до какого времени открыта пересдача"))
            until = timezone.make_aware(raw) if timezone.is_naive(raw) else raw
            services.grant_retake(attempt.exam, attempt.student, granted_by=request.user, expires_at=until)
            messages.success(request, _("%(name)s может пройти тест заново до %(until)s") % {
                "name": attempt.student.short_name,
                "until": timezone.localtime(until).strftime("%d.%m.%Y %H:%M")})
        elif action == "cancel_retake":
            services.cancel_retake(attempt.exam, attempt.student)
            messages.info(request, _("Пересдача отменена"))
        elif action == "resume":
            services.allow_resume(attempt)
            messages.success(request, _("%(name)s может войти заново и продолжить тест") % {"name": attempt.student.short_name})
        elif action == "stop":
            services.finish_attempt(attempt, status=Attempt.Status.TERMINATED)
            messages.success(request, _("Попытка %(name)s остановлена") % {"name": attempt.student.short_name})
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
    retake = services.active_retake(exam, request.user)
    if existing and (retake is None or existing.status == Attempt.Status.ACTIVE):
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
    return render(request, "exams/exam_start.html", {"exam": exam, "form": form, "retake": retake})


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
        "watermark": f"{request.user.short_name}, {request.user.masked_iin}",
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
    body = _json_body(request)
    kind = body.get("kind", "")
    try:
        terminated = services.register_violation(attempt, kind, detail=body.get("detail", ""))
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
    guard = str(_json_body(request).get("guard") or "")[:20]
    if guard != attempt.guard_version:
        Attempt.objects.filter(pk=attempt.pk).update(guard_version=guard)
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
    state = tutor_state(attempt)
    return render(request, "exams/attempt_result.html", {
        "attempt": attempt, "review": review,
        "violations": attempt.violations.all(),
        "violation_kinds": dict(Violation.Kind.choices),
        "tutor_state": state,
        "mistakes": _mistakes(attempt) if state != "off" else [],
        "tutor_max": ai.MAX_STUDENT_TURNS,
    })


# ---------- ИИ-наставник ----------

def tutor_state(attempt, now=None) -> str:
    """off — наставник выключен; wait — откроется после закрытия теста; ready — можно разбирать."""
    exam = attempt.exam
    if not exam.ai_tutor or attempt.status == Attempt.Status.ACTIVE:
        return "off"
    if (now or timezone.now()) < exam.closes_at:
        return "wait"
    return "ready"


def _mistakes(attempt):
    """Вопросы, где студент ошибся или не ответил: то, что можно разобрать с наставником."""
    items = (attempt.items.select_related("question", "selected", "tutor")
             .prefetch_related("question__choices"))
    result = []
    for item in items:
        if item.is_correct:
            continue
        choices = {c.id: c for c in item.question.choices.all()}
        ordered = [choices[cid] for cid in item.choice_order if cid in choices]
        session = getattr(item, "tutor", None)
        result.append({"item": item, "choices": ordered,
                       "messages": session.messages if session else [],
                       "solved": bool(session and session.solved)})
    return result


@login_required
@require_POST
def attempt_tutor(request, pk, item_pk):
    attempt = _student_attempt(request, pk)
    if tutor_state(attempt) != "ready":
        return JsonResponse({"error": str(_("Наставник откроется после закрытия теста."))}, status=403)
    item = get_object_or_404(AttemptItem.objects.select_related("question", "selected"),
                             pk=item_pk, attempt=attempt)
    if item.is_correct:
        return JsonResponse({"error": str(_("На этот вопрос вы ответили верно."))}, status=400)
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        payload = {}
    text = str(payload.get("message", "")).strip()[:800]
    session, _created = TutorSession.objects.get_or_create(item=item)
    if session.solved:
        return JsonResponse({"messages": session.messages, "solved": True, "left": 0})
    if text:
        if session.student_turns >= ai.MAX_STUDENT_TURNS:
            return JsonResponse({"error": str(_("Разбор этого вопроса окончен. Обсудите его с преподавателем."))}, status=400)
        session.messages.append({"role": "user", "content": text})
    elif session.messages:
        return JsonResponse({"messages": session.messages, "solved": session.solved,
                             "left": ai.MAX_STUDENT_TURNS - session.student_turns})
    choices = {c.id: c for c in item.question.choices.all()}
    ordered = [choices[cid] for cid in item.choice_order if cid in choices]
    options = [c.text for c in ordered]
    correct = next(i for i, c in enumerate(ordered) if c.is_correct)
    picked = next((i for i, c in enumerate(ordered) if c.id == item.selected_id), None)
    try:
        reply, solved, model = ai.tutor_reply(question_text=item.question.text, options=options,
                                              picked=picked, correct=correct,
                                              history=session.messages, request=request)
    except ai.AIUnavailable:
        if text:
            session.messages.pop()
        return JsonResponse({"error": str(_("Наставник сейчас недоступен. Попробуйте через минуту."))}, status=503)
    session.messages.append({"role": "assistant", "content": reply})
    session.solved = solved
    session.model = model
    session.save()
    return JsonResponse({"messages": session.messages, "solved": solved,
                         "left": ai.MAX_STUDENT_TURNS - session.student_turns})
