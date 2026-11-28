import secrets
from django.utils.translation import gettext_lazy as _
from django import forms
from django.utils import timezone

from .models import Exam, Question, Subject

MAX_CHOICES = 6
MAX_UPLOAD_MB = 10


def lock_subject(form, exam):
    """Вопрос пишется внутри теста: дисциплина берётся из теста и не меняется."""
    if exam is not None:
        form.fields["subject"].queryset = Subject.objects.filter(pk=exam.subject_id)
        form.fields["subject"].initial = exam.subject_id
        form.fields["subject"].disabled = True


class QuestionForm(forms.Form):
    """Вопрос вводится простым текстом. Формулы — в LaTeX между $…$."""

    subject = forms.ModelChoiceField(Subject.objects.all(), label=_("Дисциплина"),
                                     empty_label=_("Выберите дисциплину"))
    topic = forms.CharField(label=_("Тема"), required=False, max_length=150)
    difficulty = forms.TypedChoiceField(label=_("Сложность"), coerce=int,
                                        choices=Question.Difficulty.choices, initial=2)
    text = forms.CharField(label=_("Текст вопроса"), widget=forms.Textarea(attrs={"rows": 4}))
    image = forms.ImageField(label=_("Рисунок (необязательно)"), required=False)
    correct = forms.TypedChoiceField(
        label=_("Правильный ответ"), coerce=int, widget=forms.RadioSelect,
        choices=[(i, str(i)) for i in range(MAX_CHOICES)], initial=0)

    def __init__(self, *args, question=None, exam=None, keep=None, **kwargs):
        super().__init__(*args, **kwargs)
        lock_subject(self, exam)
        if keep and not self.is_bound:
            # Следующий вопрос той же серии: тема, сложность и дисциплина остаются.
            names = ("topic", "difficulty") if exam is not None else ("topic", "difficulty", "subject")
            for name in names:
                if keep.get(name):
                    self.initial[name] = keep.get(name)
        for i in range(MAX_CHOICES):
            self.fields[f"choice_{i}"] = forms.CharField(
                label=_("Вариант %(n)s") % {"n": i + 1}, required=i < 2, max_length=1000)
        if question is not None and not self.is_bound:
            self.initial.update(subject=question.subject_id, topic=question.topic,
                                difficulty=question.difficulty, text=question.text)
            for i, choice in enumerate(question.choices.all()[:MAX_CHOICES]):
                self.initial[f"choice_{i}"] = choice.text
                if choice.is_correct:
                    self.initial["correct"] = i

    @property
    def choice_rows(self):
        return [(i, self[f"choice_{i}"]) for i in range(MAX_CHOICES)]

    def choices_data(self) -> list[tuple[str, bool]]:
        correct = self.cleaned_data["correct"]
        return [(self.cleaned_data.get(f"choice_{i}", ""), i == correct)
                for i in range(MAX_CHOICES) if self.cleaned_data.get(f"choice_{i}")]

    def clean(self):
        data = super().clean()
        idx = data.get("correct")
        if idx is not None and not data.get(f"choice_{idx}"):
            self.add_error("correct", _("Правильным отмечен пустой вариант"))
        return data


class ImportForm(forms.Form):
    subject = forms.ModelChoiceField(Subject.objects.all(), label=_("Дисциплина"),
                                     empty_label=_("Выберите дисциплину"))
    difficulty = forms.TypedChoiceField(label=_("Сложность всех вопросов"), coerce=int,
                                        choices=Question.Difficulty.choices, initial=2)
    file = forms.FileField(label=_("Файл .docx или .txt"))
    dry_run = forms.BooleanField(label=_("Только проверить, ничего не сохранять"), required=False)

    def __init__(self, *args, exam=None, **kwargs):
        super().__init__(*args, **kwargs)
        lock_subject(self, exam)

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(_("Файл больше %(mb)s МБ") % {"mb": MAX_UPLOAD_MB})
        return f


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ["name"]

    def clean_name(self):
        # Регистр сравниваем в Python: SQLite не умеет без учёта регистра в кириллице.
        name = " ".join(self.cleaned_data["name"].split())
        key = name.casefold()
        if any(s.casefold() == key for s in Subject.objects.values_list("name", flat=True)):
            raise forms.ValidationError(_("Такая дисциплина уже есть"))
        return name


class ExamForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = ["title", "subject", "question_count", "duration_minutes",
                  "opens_at", "closes_at", "groups", "access_code", "allowed_ip_prefix",
                  "max_violations", "show_review", "ai_tutor", "is_published"]
        widgets = {
            "opens_at": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                            format="%Y-%m-%dT%H:%M"),
            "closes_at": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                             format="%Y-%m-%dT%H:%M"),
        }

    SOURCES = [
        ("manual", _("Написать вопросы на сайте")),
        ("word", _("Загрузить вопросы из Word")),
    ]
    source = forms.ChoiceField(choices=SOURCES, initial="manual", widget=forms.RadioSelect,
                               required=False, label=_("Как добавить вопросы"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].empty_label = _("Выберите дисциплину")
        self.fields["question_count"].help_text = _("Сколько вопросов получит каждый студент. Если в тесте их больше, каждому достанется свой случайный набор")
        if self.instance.pk:
            del self.fields["source"]
        elif not self.is_bound:
            now = timezone.localtime().replace(second=0, microsecond=0)
            self.initial.setdefault("opens_at", now)
            self.initial.setdefault("closes_at", now + timezone.timedelta(hours=2))
            self.initial.setdefault("question_count", 10)
            # Новый тест сначала черновик: студенты увидят его после публикации.
            self.initial.setdefault("is_published", False)
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            self.initial.setdefault("access_code", "".join(secrets.choice(alphabet) for _ in range(6)))

    def clean_groups(self):
        """«ИС-99, ,ис-99» → «ИС-99»: без пустых, без повторов, в верхнем регистре."""
        groups = []
        for raw in self.cleaned_data.get("groups", "").replace(";", ",").split(","):
            group = " ".join(raw.split()).upper()
            if group and group not in groups:
                groups.append(group)
        return ", ".join(groups)

    def clean(self):
        data = super().clean()
        opens, closes = data.get("opens_at"), data.get("closes_at")
        if opens and closes and opens >= closes:
            self.add_error("closes_at", _("Тест должен закрываться позже, чем открывается"))
        if data.get("question_count") == 0:
            self.add_error("question_count", _("Нужен хотя бы один вопрос"))
        if data.get("duration_minutes") == 0:
            self.add_error("duration_minutes", _("Время должно быть больше нуля"))
        return data


class StartExamForm(forms.Form):
    code = forms.CharField(label=_("Код доступа"), max_length=12,
                           widget=forms.TextInput(attrs={"autocomplete": "off",
                                                         "autocapitalize": "characters"}))
