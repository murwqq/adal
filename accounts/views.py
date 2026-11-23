from django.utils.translation import gettext_lazy as _
from django.contrib import messages
import secrets

from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import captcha
from . import stats
from .forms import AvatarForm, LoginForm, ProfileForm, RegistrationForm

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
                return redirect(target or "dashboard")
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


@login_required
def profile_view(request):
    """Обзор: прогресс студента или работа преподавателя. Формы живут в настройках."""
    user = request.user
    overview = stats.teacher_overview(user) if user.is_teacher else stats.student_overview(user)
    return render(request, "accounts/profile.html", {"o": overview})


@login_required
@never_cache
def settings_view(request):
    user = request.user
    action = request.POST.get("action") if request.method == "POST" else None
    profile_form = ProfileForm(request.POST if action == "profile" else None, instance=user)
    avatar_form = AvatarForm(request.POST if action == "avatar" else None,
                             request.FILES if action == "avatar" else None)
    password_form = PasswordChangeForm(user, request.POST if action == "password" else None)
    password_form.fields["new_password1"].help_text = ""  # правила уже написаны в описании раздела
    back = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(back, {request.get_host()}):
        back = ""

    if action == "profile" and profile_form.is_valid():
        profile_form.save()
        messages.success(request, _("Данные сохранены"))
        return redirect("accounts:settings")
    if action == "avatar":
        if avatar_form.is_valid():
            if user.avatar:
                user.avatar.delete(save=False)
            user.avatar.save(f"u{user.pk}_{secrets.token_hex(4)}.jpg",
                             ContentFile(avatar_form.cleaned_data["avatar"]), save=True)
            messages.success(request, _("Фото обновлено"))
            return redirect(back or "accounts:settings")
        if back:
            for error in avatar_form.errors.get("avatar", []):
                messages.error(request, error)
            return redirect(back)
    if action == "avatar_delete" and user.avatar:
        user.avatar.delete(save=True)
        messages.info(request, _("Фото удалено"))
        return redirect("accounts:settings")
    if action == "password" and password_form.is_valid():
        password_form.save()
        update_session_auth_hash(request, password_form.user)
        messages.success(request, _("Пароль изменён"))
        return redirect("accounts:settings")

    return render(request, "accounts/settings.html", {
        "profile_form": profile_form, "avatar_form": avatar_form, "password_form": password_form,
        "password_open": action == "password",
    })
