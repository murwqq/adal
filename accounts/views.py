from django.utils.translation import gettext_lazy as _
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import captcha
from .forms import LoginForm, RegistrationForm

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
