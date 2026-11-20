from exams.analysis import (analyze_items, difficulty_index, discrimination_index, lint_question,
                            score_summary)

QUESTIONS = {1: {"text": "2+2", "choices": [(10, "4", True), (11, "5", False),
                                            (12, "22", False), (13, "0", False)]}}


def test_difficulty_index_edges():
    assert difficulty_index(3, 4) == 0.75
    assert difficulty_index(0, 0) == 0.0


def test_discrimination_needs_sample():
    assert discrimination_index([(90, True), (10, False)]) is None


def test_discrimination_good_item():
    rows = [(s, s >= 50) for s in range(0, 100, 10)]
    assert discrimination_index(rows) == 1.0


def test_negative_discrimination_flags_wrong_key():
    # Сильные (высокий балл) отвечают неверно, слабые — верно.
    answers = [(score, {1: 10 if score < 50 else 11}) for score in range(0, 100, 10)]
    stat = analyze_items(QUESTIONS, answers)[0]
    assert stat.d < 0
    assert any("в ключе ошибка" in f for f in stat.flags)


def test_dead_distractors_and_easy_item():
    answers = [(80, {1: 10}) for _ in range(10)]
    stat = analyze_items(QUESTIONS, answers)[0]
    assert stat.p == 1.0
    assert any("Слишком лёгкий" in f for f in stat.flags)
    assert any("Неработающих" in f for f in stat.flags)
    assert stat.choices[0].share == 1.0


def test_small_sample_gives_no_conclusions():
    stat = analyze_items(QUESTIONS, [(50, {1: 10})])[0]
    assert stat.flags == ["Мало ответов для выводов"]


def test_unanswered_question_is_counted_as_wrong():
    stat = analyze_items(QUESTIONS, [(0, {1: None})] * 5)[0]
    assert stat.answered == 5 and stat.correct == 0


def test_score_summary():
    s = score_summary([0, 50, 100, 95])
    assert s["count"] == 4 and s["median"] == 72.5
    assert s["buckets"][9] == 2 and s["buckets"][0] == 1
    assert score_summary([])["mean"] == 0


def test_lint_finds_giveaways():
    problems = lint_question("Что такое ООП?", [
        ("Объектно-ориентированное программирование, основанное на объектах", True),
        ("Язык", False), ("Сеть", False)])
    assert any("длиннее" in p for p in problems)
    assert lint_question("", [("a", True)])  # пусто и мало вариантов
    assert any("одинаковые" in p for p in lint_question("q", [("a", True), ("A", False)]))
    assert any("перечисленные" in p for p in lint_question(
        "q", [("все перечисленные", True), ("b", False), ("c", False)]))
