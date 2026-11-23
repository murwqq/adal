"""Анализ ответов (классическая теория тестов). Чистые функции без Django.

p — индекс трудности: доля верных ответов.
D — индекс дискриминации: p в сильной группе (верхние 27 %) минус p в слабой.
Дистрактор — неверный вариант; если его почти никто не выбирает, он не работает.
"""
from django.utils.translation import gettext_lazy as _
import statistics
from dataclasses import dataclass, field

GROUP_SHARE = 0.27
TOO_EASY = 0.9
TOO_HARD = 0.2
WEAK_DISCRIMINATION = 0.2
DEAD_DISTRACTOR = 0.05
MIN_SAMPLE = 5
LONGEST_RATIO = 1.5


@dataclass
class ChoiceStat:
    choice_id: int
    text: str
    is_correct: bool
    picked: int = 0
    share: float = 0.0


@dataclass
class QuestionStat:
    question_id: int
    text: str
    answered: int = 0
    correct: int = 0
    p: float = 0.0
    d: float | None = None
    choices: list[ChoiceStat] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def difficulty_index(correct: int, total: int) -> float:
    return correct / total if total else 0.0


def discrimination_index(rows: list[tuple[float, bool]]) -> float | None:
    """rows: (общий балл студента, ответил ли верно на этот вопрос)."""
    n = len(rows)
    k = max(1, round(n * GROUP_SHARE))
    if n < MIN_SAMPLE or 2 * k > n:
        return None
    ranked = sorted(rows, key=lambda r: r[0])
    lower = sum(1 for _, ok in ranked[:k] if ok) / k
    upper = sum(1 for _, ok in ranked[-k:] if ok) / k
    return round(upper - lower, 2)


def flags_for(stat: QuestionStat) -> list[str]:
    flags = []
    if stat.answered < MIN_SAMPLE:
        return [_("Мало ответов для выводов")]
    if stat.p >= TOO_EASY:
        flags.append(_("Слишком лёгкий: почти все отвечают верно"))
    if stat.p <= TOO_HARD:
        flags.append(_("Слишком трудный. Проверьте, верно ли отмечен ответ"))
    if stat.d is not None and stat.d < 0:
        flags.append(_("Сильные студенты ошибаются чаще слабых. Скорее всего, в ключе ошибка"))
    elif stat.d is not None and stat.d < WEAK_DISCRIMINATION:
        flags.append(_("Плохо отделяет знающих от незнающих"))
    dead = [c for c in stat.choices if not c.is_correct and c.share < DEAD_DISTRACTOR]
    if dead:
        flags.append(_("Неработающих неверных вариантов: %(n)s (их почти не выбирают)") % {"n": len(dead)})
    return flags


def analyze_items(questions: dict[int, dict], answers: list[tuple[int, dict[int, int | None]]]
                  ) -> list[QuestionStat]:
    """questions: {qid: {"text": str, "choices": [(cid, text, is_correct), ...]}}
    answers: [(общий балл, {qid: выбранный cid или None}), ...] — по одной строке на попытку."""
    result = []
    for qid, q in questions.items():
        stat = QuestionStat(question_id=qid, text=q["text"])
        correct_ids = {cid for cid, _, ok in q["choices"] if ok}
        picks: dict[int, int] = {cid: 0 for cid, _, _ in q["choices"]}
        rows = []
        for total, chosen in answers:
            if qid not in chosen:
                continue
            cid = chosen[qid]
            stat.answered += 1
            ok = cid in correct_ids
            stat.correct += ok
            if cid in picks:
                picks[cid] += 1
            rows.append((total, ok))
        stat.p = round(difficulty_index(stat.correct, stat.answered), 2)
        stat.d = discrimination_index(rows)
        stat.choices = [
            ChoiceStat(cid, text, ok, picks[cid],
                       round(picks[cid] / stat.answered, 2) if stat.answered else 0.0)
            for cid, text, ok in q["choices"]
        ]
        stat.flags = flags_for(stat)
        result.append(stat)
    return result


def percent(score: int, total: int) -> int:
    """Процент с округлением «половина вверх»: 1 из 8 = 13 %, 5 из 8 = 63 %.
    Встроенный round() в Python округляет 12,5 до 12, а 87,5 до 88, отсюда разнобой."""
    return (200 * score + total) // (2 * total) if total else 0


def mean_percent(values: list[int]) -> int | None:
    """Среднее из целых процентов, тоже «половина вверх»."""
    return (2 * sum(values) + len(values)) // (2 * len(values)) if values else None


def score_summary(percents: list[int]) -> dict:
    """Среднее, медиана, разброс и гистограмма по 10 корзинам (0–9 %, …, 90–100 %)."""
    buckets = [0] * 10
    for value in percents:
        buckets[min(value // 10, 9)] += 1
    if not percents:
        return {"count": 0, "mean": 0, "median": 0, "stdev": 0, "buckets": buckets}
    return {
        "count": len(percents),
        "mean": round(statistics.fmean(percents), 1),
        "median": round(float(statistics.median(percents)), 1),
        "stdev": round(statistics.pstdev(percents), 1),
        "buckets": buckets,
    }


def lint_question(text: str, choices: list[tuple[str, bool]]) -> list[str]:
    """Подсказки преподавателю: что в вопросе может выдать правильный ответ."""
    problems = []
    if not text.strip():
        problems.append(_("Пустой текст вопроса"))
    if len(choices) < 2:
        problems.append(_("Нужно минимум два варианта ответа"))
    correct = [t for t, ok in choices if ok]
    if len(correct) != 1:
        problems.append(_("Должен быть ровно один правильный ответ"))
    normalized = [t.strip().lower() for t, _ in choices]
    if len(set(normalized)) != len(normalized):
        problems.append(_("Есть одинаковые варианты ответа"))
    if len(correct) == 1 and len(choices) >= 3:
        wrong_lengths = [len(t) for t, ok in choices if not ok]
        if len(correct[0]) > LONGEST_RATIO * max(wrong_lengths):
            problems.append(_("Правильный ответ заметно длиннее остальных, по длине его легко угадать"))
    lowered = " ".join(normalized)
    if "все перечисленн" in lowered or "все ответы верн" in lowered:
        problems.append(_("Вариант «все перечисленные» облегчает угадывание"))
    return problems
