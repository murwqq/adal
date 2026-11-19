from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from .iin import mask_iin


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, iin, password=None, **extra):
        if not iin:
            raise ValueError("Нужен ИИН")
        user = self.model(iin=iin, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, iin, password=None, **extra):
        extra.setdefault("role", User.Role.ADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(iin, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Пользователь входит по ИИН. Роль определяет, что ему доступно."""

    class Role(models.TextChoices):
        STUDENT = "student", "Студент"
        TEACHER = "teacher", "Преподаватель"
        ADMIN = "admin", "Администратор"

    class Degree(models.TextChoices):
        BACHELOR = "bachelor", "Бакалавриат"
        MASTER = "master", "Магистратура"
        DOCTOR = "doctor", "Докторантура"

    class ApplicationType(models.TextChoices):
        FIRST = "first", "Первое высшее образование"
        SHORT = "short", "Сокращённая программа (на базе ТиПО)"
        SECOND = "second", "Второе высшее образование"

    class DocType(models.TextChoices):
        ID_CARD = "id_card", "Удостоверение личности РК"
        PASSPORT = "passport", "Паспорт"
        RESIDENCE = "residence", "Вид на жительство"

    class Language(models.TextChoices):
        KAZAKH = "kk", "Қазақша"
        RUSSIAN = "ru", "Русский"
        ENGLISH = "en", "English"

    iin = models.CharField("ИИН", max_length=12, unique=True)
    first_name = models.CharField("Имя", max_length=80)
    last_name = models.CharField("Фамилия", max_length=80)
    patronymic = models.CharField("Отчество", max_length=80, blank=True)
    email = models.EmailField("Эл. почта", blank=True)
    role = models.CharField("Роль", max_length=10, choices=Role.choices, default=Role.STUDENT)
    group = models.CharField("Группа", max_length=30, blank=True)

    # Поля анкеты абитуриента (как в Platonus)
    interface_language = models.CharField(
        "Язык интерфейса", max_length=2, choices=Language.choices, default=Language.RUSSIAN)
    degree = models.CharField("Академическая степень", max_length=10,
                              choices=Degree.choices, default=Degree.BACHELOR)
    application_type = models.CharField("Заявление", max_length=10,
                                        choices=ApplicationType.choices, default=ApplicationType.FIRST)
    citizenship = models.CharField("Страна", max_length=60, default="Казахстан")
    doc_type = models.CharField("Документ", max_length=10, choices=DocType.choices,
                                default=DocType.ID_CARD)
    doc_number = models.CharField("Номер документа", max_length=20, blank=True)
    study_language = models.CharField("Язык обучения", max_length=2,
                                      choices=Language.choices, default=Language.KAZAKH)
    birth_date = models.DateField("Дата рождения", null=True, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "iin"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.last_name, self.first_name, self.patronymic) if p)

    @property
    def short_name(self) -> str:
        initials = "".join(f"{p[0]}." for p in (self.first_name, self.patronymic) if p)
        return f"{self.last_name} {initials}".strip()

    @property
    def masked_iin(self) -> str:
        return mask_iin(self.iin)

    @property
    def is_teacher(self) -> bool:
        return self.role in (self.Role.TEACHER, self.Role.ADMIN)

    @property
    def is_student(self) -> bool:
        return self.role == self.Role.STUDENT
