from django.utils.translation import gettext_lazy as _
from django.utils.translation import get_language, gettext_noop
from django import forms
from django.contrib.auth import password_validation

from . import captcha
from .iin import IINError, parse_iin
from .models import User

COUNTRIES = [
    gettext_noop("Казахстан"),
    gettext_noop("Узбекистан"),
    gettext_noop("Кыргызстан"),
    gettext_noop("Россия"),
    gettext_noop("Туркменистан"),
    gettext_noop("Таджикистан"),
    gettext_noop("Монголия"),
    gettext_noop("Китай"),
    gettext_noop("Турция"),
    gettext_noop("Другая страна"),
]


class LoginForm(forms.Form):
    iin = forms.CharField(
        label=_("ИИН"), max_length=12,
        widget=forms.TextInput(attrs={
            "inputmode": "numeric", "autocomplete": "username",
            "pattern": r"\d{12}", "autofocus": True,
        }),
    )
    password = forms.CharField(
        label=_("Пароль"),
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class RegistrationForm(forms.ModelForm):
    """Анкета абитуриента — набор полей повторяет регистрацию в Platonus."""

    citizenship = forms.ChoiceField(label=_("Гражданство"), choices=[(c, _(c)) for c in COUNTRIES])
    password1 = forms.CharField(
        label=_("Пароль"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=_("Не короче 8 символов, не только цифры."),
    )
    password2 = forms.CharField(
        label=_("Пароль ещё раз"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    captcha = forms.CharField(label=_("Символы с картинки"), max_length=8,
                              widget=forms.TextInput(attrs={"autocomplete": "off"}))
    terms = forms.BooleanField(
        label=_("Я принимаю правила и условия платформы"),
        error_messages={"required": _("Без согласия с правилами регистрация невозможна")},
    )

    class Meta:
        model = User
        fields = [
            "degree", "application_type", "citizenship",
            "doc_type", "doc_number", "study_language", "iin",
            "last_name", "first_name", "patronymic", "email",
        ]
        widgets = {
            "iin": forms.TextInput(attrs={"inputmode": "numeric", "pattern": r"\d{12}",
                                          "maxlength": 12, "autocomplete": "off"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
        }
        labels = {
            "patronymic": _("Отчество"),
            "application_type": _("Форма поступления"),
            "citizenship": _("Гражданство"),
            "doc_type": _("Документ"),
        }

    def __init__(self, *args, expected_captcha=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.expected_captcha = expected_captcha
        self.fields["email"].required = True
        self.fields["doc_number"].required = True

    def clean_iin(self):
        value = self.cleaned_data["iin"].strip()
        try:
            self.iin_info = parse_iin(value)
        except IINError as exc:
            raise forms.ValidationError(str(exc)) from exc
        if User.objects.filter(iin=value).exists():
            raise forms.ValidationError(_("Пользователь с таким ИИН уже зарегистрирован"))
        return value

    def clean_captcha(self):
        value = self.cleaned_data["captcha"]
        if not captcha.check(self.expected_captcha, value):
            raise forms.ValidationError(_("Символы не совпали. Введите код с новой картинки"))
        return value

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", _("Пароли не совпадают"))
        if p1:
            try:
                password_validation.validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.STUDENT
        user.interface_language = (get_language() or "ru")[:2]
        user.birth_date = self.iin_info.birth_date
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


# ---------- Личный кабинет ----------

AVATAR_MAX_MB = 5
AVATAR_SIZE = 320


class ProfileForm(forms.ModelForm):
    """ФИО меняет только преподаватель и администратор: у студента оно
    печатается в водяном знаке теста и должно совпадать с документами."""

    class Meta:
        model = User
        fields = ["last_name", "first_name", "patronymic", "email"]
        widgets = {"email": forms.EmailInput(attrs={"autocomplete": "email"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.is_teacher:
            for name in ("last_name", "first_name", "patronymic"):
                self.fields[name].disabled = True


class AvatarForm(forms.Form):
    avatar = forms.ImageField(label=_("Новое фото"))

    def clean_avatar(self):
        from io import BytesIO
        from PIL import Image, ImageOps
        upload = self.cleaned_data["avatar"]
        if upload.size > AVATAR_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(_("Файл больше %(mb)s МБ") % {"mb": AVATAR_MAX_MB})
        upload.seek(0)
        image = ImageOps.exif_transpose(Image.open(upload)).convert("RGB")
        image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
        out = BytesIO()
        image.save(out, "JPEG", quality=86, optimize=True)
        return out.getvalue()
