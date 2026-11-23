"""Модель данных: дисциплина → банк вопросов → тест → попытка → ответы."""
from django.utils.translation import gettext_lazy as _
from django.conf import settings
from django.db import models
from django.utils import timezone


class Subject(models.Model):
    name = models.CharField(_("Название"), max_length=150, unique=True)

    class Meta:
        verbose_name = _("дисциплина")
        verbose_name_plural = _("дисциплины")
        ordering = ["name"]

    def __str__(self):
        return self.name


class Question(models.Model):
    """Вопрос банка. Текст хранится только как простой текст:
    никакого жирного/курсива, поэтому оформление не выдаёт правильный ответ.
    Формулы пишутся в LaTeX между знаками $…$ и рисуются одинаково у всех."""

    class Difficulty(models.IntegerChoices):
        EASY = 1, _("Лёгкий")
        MEDIUM = 2, _("Средний")
        HARD = 3, _("Сложный")

    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="questions",
                                verbose_name=_("Дисциплина"))
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                               related_name="questions", verbose_name=_("Автор"))
    topic = models.CharField(_("Тема"), max_length=150, blank=True)
    text = models.TextField(_("Текст вопроса"))
    image = models.ImageField(_("Рисунок"), upload_to="questions/", blank=True)
    difficulty = models.PositiveSmallIntegerField(_("Сложность"), choices=Difficulty.choices,
                                                  default=Difficulty.MEDIUM)
    is_active = models.BooleanField(_("В банке"), default=True)
    created_at = models.DateTimeField(_("Создан"), auto_now_add=True)

    class Meta:
        verbose_name = _("вопрос")
        verbose_name_plural = _("вопросы")
        ordering = ["-created_at"]

    def __str__(self):
        return self.text[:80]


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField(_("Вариант ответа"), max_length=1000)
    is_correct = models.BooleanField(_("Правильный"), default=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.text[:60]


class Exam(models.Model):
    """Тест: свой набор вопросов и правила, по которым из него собирается
    вариант для каждого студента."""

    title = models.CharField(_("Название"), max_length=200)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="exams",
                                verbose_name=_("Дисциплина"))
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="exams")
    topic = models.CharField(_("Только тема"), max_length=150, blank=True,
                             help_text=_("Оставьте пустым, чтобы брать вопросы по всей дисциплине"))
    question_count = models.PositiveSmallIntegerField(_("Вопросов в варианте"), default=20)
    duration_minutes = models.PositiveSmallIntegerField(_("Время, минут"), default=30)
    opens_at = models.DateTimeField(_("Открывается"))
    closes_at = models.DateTimeField(_("Закрывается"))
    groups = models.CharField(_("Группы"), max_length=300, blank=True,
                              help_text=_("Через запятую, например: ИС-21, ИС-22. Если оставить пустым, тест увидят все группы"))
    access_code = models.CharField(_("Код доступа"), max_length=12,
                                   help_text=_("Преподаватель говорит его вслух в аудитории"))
    allowed_ip_prefix = models.CharField(
        _("Разрешённая сеть"), max_length=60, blank=True,
        help_text=_("Начало IP-адресов компьютерного класса, например 10.12.5. Если оставить пустым, тест откроется из любой сети"))
    max_violations = models.PositiveSmallIntegerField(
        _("Нарушений до завершения"), default=3,
        help_text=_("Уход со страницы, выход из полноэкранного режима и т. п."))
    show_review = models.BooleanField(_("Показать студенту разбор после теста"), default=False)
    questions = models.ManyToManyField("Question", related_name="exams", blank=True,
                                       verbose_name=_("Вопросы теста"))
    is_published = models.BooleanField(_("Опубликован"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("тест")
        verbose_name_plural = _("тесты")
        ordering = ["-opens_at"]

    def __str__(self):
        return self.title

    @property
    def group_list(self) -> list[str]:
        return [g.strip().upper() for g in self.groups.split(",") if g.strip()]

    def is_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.is_published and self.opens_at <= now <= self.closes_at


class CurrentAttemptManager(models.Manager):
    """По умолчанию видны только текущие попытки, без заменённых пересдачей."""

    def get_queryset(self):
        return super().get_queryset().filter(archived=False)


class Attempt(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Идёт")
        FINISHED = "finished", _("Завершена")
        EXPIRED = "expired", _("Время вышло")
        TERMINATED = "terminated", _("Остановлена за нарушения")

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="attempts")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateTimeField(default=timezone.now)
    deadline = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(default=0)
    max_score = models.PositiveSmallIntegerField(default=0)
    seed = models.BigIntegerField()
    session_key = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    violation_count = models.PositiveSmallIntegerField(default=0)
    # Попытка, которую заменила пересдача. Остаётся в истории, но не в результатах.
    archived = models.BooleanField(default=False)

    objects = CurrentAttemptManager()
    all_objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exam", "student"], condition=models.Q(archived=False),
                                    name="one_current_attempt_per_student"),
        ]
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.student} — {self.exam}"

    @property
    def percent(self) -> int:
        from .analysis import percent
        return percent(self.score, self.max_score)


class AttemptItem(models.Model):
    """Вопрос в варианте конкретного студента: свой порядок вопросов и ответов."""

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="items")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="attempt_items")
    position = models.PositiveSmallIntegerField()
    choice_order = models.JSONField(default=list)
    selected = models.ForeignKey(Choice, on_delete=models.SET_NULL, null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["attempt", "position"], name="unique_item_position"),
        ]

    @property
    def is_correct(self) -> bool:
        return bool(self.selected and self.selected.is_correct)


class Violation(models.Model):
    class Kind(models.TextChoices):
        TAB_HIDDEN = "tab_hidden", _("Переключился на другую вкладку или окно")
        WINDOW_BLUR = "window_blur", _("Окно теста потеряло фокус")
        FULLSCREEN_EXIT = "fullscreen_exit", _("Вышел из полноэкранного режима")
        COPY = "copy", _("Пытался скопировать текст")
        PASTE = "paste", _("Пытался вставить текст")
        CONTEXT_MENU = "context_menu", _("Открывал контекстное меню (правая кнопка)")
        DEVTOOLS = "devtools", _("Нажимал клавиши инструментов разработчика")
        PRINT_SCREEN = "print_screen", _("Нажимал Print Screen")
        OTHER_SESSION = "other_session", _("Открыл тест на другом компьютере или в другом браузере")
        OFFLINE = "offline", _("Пропадала связь со страницей теста")

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="violations")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]


class Retake(models.Model):
    """Разрешение администратора пройти тест заново, даже если тест уже закрыт."""

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="retakes")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="retakes")
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(_("Пересдача доступна до"))
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def is_valid(self, now=None) -> bool:
        return self.used_at is None and (now or timezone.now()) <= self.expires_at
