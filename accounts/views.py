from django.utils.translation import gettext_lazy as _
from django.contrib import messages
import secrets

from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import translation
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import captcha
from .forms import AvatarForm, InterfaceForm, LoginForm, ProfileForm, RegistrationForm

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 10 * 60


def _fail_key(iin: str) -> str:
    return f"login-fail:{iin}"


@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        iin = form.cleaned_data["iin"].strip()
        failures = cache.get(_fail_key(iin), 0)
        if failures >= MAX_FAILED_LOGINS:
            form.add_error(None, _("Слишком много неудачных попыток. Подождите 10 минут."))
        else:
            user = authenticate(request, iin=iin, password=form.cleaned_data["password"])
            if user is None:
                cache.set(_fail_key(iin), failures + 1, LOCKOUT_SECONDS)
                form.add_error(None, _("Неверный ИИН или пароль"))
            else:
                cache.delete(_fail_key(iin))
                login(request, user)
                target = request.GET.get("next", "")
                if not url_has_allowed_host_and_scheme(target, {request.get_host()}):
                    target = "dashboard"
                return _with_language(redirect(target or "dashboard"), user.interface_language)
    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@never_cache
def register_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    expected = request.session.get(captcha.SESSION_KEY)
    form = RegistrationForm(request.POST or None, expected_captcha=expected)
    if request.method == "POST":
        # Одна картинка — одна попытка, чтобы капчу нельзя было перебирать.
        request.session.pop(captcha.SESSION_KEY, None)
        if form.is_valid():
            user = form.save()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            messages.success(request, _("Регистрация завершена. Добро пожаловать!"))
            return redirect("dashboard")
    return render(request, "accounts/register.html", {"form": form})


@never_cache
def captcha_image(request):
    code = captcha.new_code()
    request.session[captcha.SESSION_KEY] = code
    return HttpResponse(captcha.render_png(code), content_type="image/png")


def rules_view(request):
    return render(request, "accounts/rules.html")


def _with_language(response, language):
    """Включает язык интерфейса, выбранный в личном кабинете."""
    if language in dict(settings.LANGUAGES):
        translation.activate(language)
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language,
                            max_age=settings.LANGUAGE_COOKIE_AGE or 365 * 24 * 3600,
                            samesite="Lax")
    return response


def _profile_stats(user):
    """Цифры для шапки кабинета: у студента его тесты, у преподавателя его банк."""
    from django.db.models import Count, Q
    from exams.models import Attempt, Exam, Question
    if user.is_teacher:
        exams = Exam.objects.filter(author=user)
        return [
            (Question.objects.filter(author=user, is_active=True).count(), _("вопросов в банке")),
            (exams.count(), _("тестов создано")),
            (Attempt.objects.filter(exam__author=user).exclude(status=Attempt.Status.ACTIVE).count(),
             _("работ студентов")),
            (Question.objects.filter(author=user, is_active=True).values("subject").distinct().count(),
             _("дисциплин")),
        ]
    done = list(Attempt.objects.filter(student=user).exclude(status=Attempt.Status.ACTIVE)
                .only("score", "max_score", "violation_count"))
    percents = [round(100 * a.score / a.max_score) for a in done if a.max_score]
    return [
        (len(done), _("тестов сдано")),
        (f"{round(sum(percents) / len(percents))}%" if percents else "—", _("средний результат")),
        (f"{max(percents)}%" if percents else "—", _("лучший результат")),
        (sum(a.violation_count for a in done), _("нарушений за всё время")),
    ]


@login_required
@never_cache
def profile_view(request):
    user = request.user
    action = request.POST.get("action") if request.method == "POST" else None
    profile_form = ProfileForm(request.POST if action == "profile" else None, instance=user)
    avatar_form = AvatarForm(request.POST if action == "avatar" else None,
                             request.FILES if action == "avatar" else None)
    interface_form = InterfaceForm(request.POST if action == "interface" else None, instance=user)
    password_form = PasswordChangeForm(user, request.POST if action == "password" else None)

    if action == "profile" and profile_form.is_valid():
        profile_form.save()
        messages.success(request, _("Данные сохранены"))
        return redirect("accounts:profile")
    if action == "avatar" and avatar_form.is_valid():
        if user.avatar:
            user.avatar.delete(save=False)
        user.avatar.save(f"u{user.pk}_{secrets.token_hex(4)}.jpg",
                         ContentFile(avatar_form.cleaned_data["avatar"]), save=True)
        messages.success(request, _("Фото обновлено"))
        return redirect("accounts:profile")
    if action == "avatar_delete" and user.avatar:
        user.avatar.delete(save=True)
        messages.info(request, _("Фото удалено"))
        return redirect("accounts:profile")
    if action == "interface" and interface_form.is_valid():
        interface_form.save()
        messages.success(request, _("Язык интерфейса сохранён"))
        return _with_language(redirect("accounts:profile"), user.interface_language)
    if action == "password" and password_form.is_valid():
        password_form.save()
        update_session_auth_hash(request, password_form.user)
        messages.success(request, _("Пароль изменён"))
        return redirect("accounts:profile")

    return render(request, "accounts/profile.html", {
        "profile_form": profile_form, "avatar_form": avatar_form,
        "interface_form": interface_form, "password_form": password_form,
        "stats": _profile_stats(user), "open_section": action,
    })
