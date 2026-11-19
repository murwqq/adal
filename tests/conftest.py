from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.iin import make_valid_iin
from accounts.models import User
from exams import services
from exams.models import Exam, Subject

PASSWORD = "test-pass-2026"


@pytest.fixture(autouse=True)
def _isolation(settings, tmp_path):
    """Каждый тест — со своей папкой файлов и пустым кэшем (счётчик неудачных входов)."""
    from django.core.cache import cache
    settings.MEDIA_ROOT = tmp_path / "media"
    cache.clear()
    yield
    cache.clear()


def _iin(prefix: str) -> str:
    for serial in range(10000):
        try:
            return make_valid_iin(f"{prefix}{serial:04d}")
        except ValueError:
            continue
    raise AssertionError("no iin")


@pytest.fixture
def teacher(db):
    return User.objects.create_user(_iin("7001014"), PASSWORD, first_name="Гульнар",
                                    last_name="Сейткали", role=User.Role.TEACHER)


@pytest.fixture
def student(db):
    return User.objects.create_user(_iin("0403055"), PASSWORD, first_name="Ержан",
                                    last_name="Касымов", group="ИС-21")


@pytest.fixture
def other_student(db):
    return User.objects.create_user(_iin("0407086"), PASSWORD, first_name="Алия",
                                    last_name="Серикова", group="ИС-22")


@pytest.fixture
def subject(db, teacher):
    subj = Subject.objects.create(name="Python")
    for i in range(6):
        services.save_question(
            subject=subj, author=teacher, text=f"Вопрос номер {i}",
            topic="Основы" if i < 4 else "ООП", difficulty=1 + i % 3,
            choices=[(f"верный {i}", True), (f"неверный A{i}", False),
                     (f"неверный B{i}", False), (f"неверный C{i}", False)])
    return subj


@pytest.fixture
def exam(db, subject, teacher):
    now = timezone.now()
    return Exam.objects.create(
        title="Рубежный контроль", subject=subject, author=teacher, question_count=4,
        duration_minutes=10, opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=1), access_code="ABC123", max_violations=3)
