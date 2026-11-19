"""Модель данных: дисциплина → банк вопросов → тест → попытка → ответы."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Subject(models.Model):
    name = models.CharField("Название", max_length=150, unique=True)

    class Meta:
        verbose_name = "дисциплина"
        verbose_name_plural = "дисциплины"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Question(models.Model):
    """Вопрос банка. Текст хранится только как простой текст:
    никакого жирного/курсива, поэтому оформление не выдаёт правильный ответ.
    Формулы пишутся в LaTeX между знаками $…$ и рисуются одинаково у всех."""

    class Difficulty(models.IntegerChoices):
        EASY = 1, "Лёгкий"
        MEDIUM = 2, "Средний"
        HARD = 3, "Сложный"

    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="questions",
                                verbose_name="Дисциплина")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
                               related_name="questions", verbose_name="Автор")
    topic = models.CharField("Тема", max_length=150, blank=True)
    text = models.TextField("Текст вопроса")
    image = models.ImageField("Рисунок", upload_to="questions/", blank=True)
    difficulty = models.PositiveSmallIntegerField("Сложность", choices=Difficulty.choices,
                                                  default=Difficulty.MEDIUM)
    is_active = models.BooleanField("В банке", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)

    class Meta:
        verbose_name = "вопрос"
        verbose_name_plural = "вопросы"
        ordering = ["-created_at"]

    def __str__(self):
        return self.text[:80]


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="choices")
    text = models.CharField("Вариант ответа", max_length=1000)
    is_correct = models.BooleanField("Правильный", default=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.text[:60]


class Exam(models.Model):
    """Тест: правила, по которым из банка собирается вариант для каждого студента."""

    title = models.CharField("Название", max_length=200)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="exams",
                                verbose_name="Дисциплина")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                               related_name="exams")
    topic = models.CharField("Только тема", max_length=150, blank=True,
                             help_text="Оставьте пустым, чтобы брать вопросы по всей дисциплине")
    question_count = models.PositiveSmallIntegerField("Вопросов в варианте", default=20)
    duration_minutes = models.PositiveSmallIntegerField("Время, минут", default=30)
    opens_at = models.DateTimeField("Открывается")
    closes_at = models.DateTimeField("Закрывается")
    groups = models.CharField("Группы", max_length=300, blank=True,
                              help_text="Через запятую, например: ИС-21, ИС-22. Пусто — все")
    access_code = models.CharField("Код доступа", max_length=12,
                                   help_text="Преподаватель говорит его вслух в аудитории")
    allowed_ip_prefix = models.CharField(
        "Разрешённая сеть", max_length=60, blank=True,
        help_text="Начало IP-адресов компьютерного класса, например 10.12.5. Пусто — любая сеть")
    max_violations = models.PositiveSmallIntegerField(
        "Нарушений до завершения", default=3,
        help_text="Уход со страницы, выход из полноэкранного режима и т. п.")
    show_review = models.BooleanField("Показать студенту разбор после теста", default=False)
    is_published = models.BooleanField("Опубликован", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "тест"
        verbose_name_plural = "тесты"
        ordering = ["-opens_at"]

    def __str__(self):
        return self.title

    @property
    def group_list(self) -> list[str]:
        return [g.strip().upper() for g in self.groups.split(",") if g.strip()]

    def is_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.is_published and self.opens_at <= now <= self.closes_at


class Attempt(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Идёт"
        FINISHED = "finished", "Завершена"
        EXPIRED = "expired", "Время вышло"
        TERMINATED = "terminated", "Остановлена за нарушения"

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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["exam", "student"], name="one_attempt_per_student"),
        ]
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.student} — {self.exam}"

    @property
    def percent(self) -> int:
        return round(100 * self.score / self.max_score) if self.max_score else 0


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
        TAB_HIDDEN = "tab_hidden", "Переключился на другую вкладку или окно"
        WINDOW_BLUR = "window_blur", "Окно теста потеряло фокус"
        FULLSCREEN_EXIT = "fullscreen_exit", "Вышел из полноэкранного режима"
        COPY = "copy", "Пытался скопировать текст"
        PASTE = "paste", "Пытался вставить текст"
        CONTEXT_MENU = "context_menu", "Открывал контекстное меню (правая кнопка)"
        DEVTOOLS = "devtools", "Нажимал клавиши инструментов разработчика"
        PRINT_SCREEN = "print_screen", "Нажимал Print Screen"
        OTHER_SESSION = "other_session", "Открыл тест на другом компьютере или в другом браузере"
        OFFLINE = "offline", "Пропадала связь со страницей теста"

    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="violations")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
