from django.utils.translation import gettext_lazy as _
from django import forms
from django.utils import timezone

from .models import Exam, Question, Subject

MAX_CHOICES = 6
MAX_UPLOAD_MB = 10


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

    def __init__(self, *args, question=None, **kwargs):
        super().__init__(*args, **kwargs)
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

    def clean_file(self):
        f = self.cleaned_data["file"]
        if f.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(_("Файл больше %(mb)s МБ") % {"mb": MAX_UPLOAD_MB})
        return f


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ["name"]


class ExamForm(forms.ModelForm):
    class Meta:
        model = Exam
        fields = ["title", "subject", "topic", "question_count", "duration_minutes",
                  "opens_at", "closes_at", "groups", "access_code", "allowed_ip_prefix",
                  "max_violations", "show_review", "is_published"]
        widgets = {
            "opens_at": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                            format="%Y-%m-%dT%H:%M"),
            "closes_at": forms.DateTimeInput(attrs={"type": "datetime-local"},
                                             format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["subject"].empty_label = _("Выберите дисциплину")
        if not self.instance.pk and not self.is_bound:
            now = timezone.localtime().replace(second=0, microsecond=0)
            self.initial.setdefault("opens_at", now)
            self.initial.setdefault("closes_at", now + timezone.timedelta(hours=2))

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
