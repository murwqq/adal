from django import forms
from django.contrib.auth import password_validation

from . import captcha
from .iin import IINError, parse_iin
from .models import User

COUNTRIES = [
    "Казахстан", "Узбекистан", "Кыргызстан", "Россия", "Туркменистан",
    "Таджикистан", "Монголия", "Китай", "Турция", "Другая страна",
]


class LoginForm(forms.Form):
    iin = forms.CharField(
        label="ИИН", max_length=12,
        widget=forms.TextInput(attrs={
            "inputmode": "numeric", "autocomplete": "username",
            "pattern": r"\d{12}", "autofocus": True,
        }),
    )
    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class RegistrationForm(forms.ModelForm):
    """Анкета абитуриента — набор полей повторяет регистрацию в Platonus."""

    citizenship = forms.ChoiceField(label="Страна", choices=[(c, c) for c in COUNTRIES])
    password1 = forms.CharField(
        label="Пароль", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Не короче 8 символов, не только цифры.",
    )
    password2 = forms.CharField(
        label="Пароль ещё раз", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    captcha = forms.CharField(label="Символы с картинки", max_length=8,
                              widget=forms.TextInput(attrs={"autocomplete": "off"}))
    terms = forms.BooleanField(
        label="Я принимаю правила и условия платформы",
        error_messages={"required": "Без согласия с правилами регистрация невозможна"},
    )

    class Meta:
        model = User
        fields = [
            "interface_language", "degree", "application_type", "citizenship",
            "doc_type", "doc_number", "study_language", "iin",
            "last_name", "first_name", "patronymic", "email",
        ]
        widgets = {
            "iin": forms.TextInput(attrs={"inputmode": "numeric", "pattern": r"\d{12}",
                                          "maxlength": 12}),
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
            raise forms.ValidationError("Пользователь с таким ИИН уже зарегистрирован")
        return value

    def clean_captcha(self):
        value = self.cleaned_data["captcha"]
        if not captcha.check(self.expected_captcha, value):
            raise forms.ValidationError("Символы не совпадают — введите код с новой картинки")
        return value

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Пароли не совпадают")
        if p1:
            try:
                password_validation.validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.STUDENT
        user.birth_date = self.iin_info.birth_date
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user
