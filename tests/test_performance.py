"""NFR-01: поиск по банку из 1000 вопросов отвечает быстрее секунды."""
import time

from exams import services
from exams.models import Choice, Question

QUESTIONS = 1000
LIMIT_SECONDS = 1.0


def test_bank_search_1000_questions_under_one_second(subject, teacher):
    questions = Question.objects.bulk_create(
        Question(subject=subject, author=teacher, text=f"Производная функции номер {i}",
                 topic="Анализ", difficulty=1 + i % 3) for i in range(QUESTIONS))
    Choice.objects.bulk_create(
        Choice(question=q, text=f"вариант {k}", is_correct=k == 0)
        for q in questions for k in range(4))
    start = time.perf_counter()
    found = list(services.search_questions(Question.objects.all(), text="ПРОИЗВОДНАЯ",
                                           sort="weak")[:20])
    elapsed = time.perf_counter() - start
    assert len(found) == 20
    assert elapsed < LIMIT_SECONDS, f"{elapsed:.3f} с"
