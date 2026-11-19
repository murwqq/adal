"""Демо-данные: python manage.py seed_demo

Создаёт администратора, двух преподавателей, студентов, дисциплины, банк
вопросов с формулами, открытый тест и несколько завершённых попыток для анализа."""
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.iin import IINError, make_valid_iin
from accounts.models import User
from exams import services
from exams.models import Attempt, Exam, Subject

DEMO_PASSWORD = "adal2026"

QUESTIONS = {
    "Программирование на Python": [
        ("Основы", 1, "Какой тип данных вернёт выражение type(3 / 2)?",
         ["float", "int", "decimal", "complex"]),
        ("Основы", 1, "Какое ключевое слово объявляет функцию?",
         ["def", "func", "function", "lambda"]),
        ("Основы", 2, "Что выведет print(len({1, 2, 2, 3}))?", ["3", "4", "2", "Ошибку"]),
        ("Коллекции", 2, "Какой метод добавляет элемент в конец списка?",
         ["append", "add", "insert", "extend"]),
        ("Коллекции", 2, "Чему равно [x * 2 for x in range(3)]?",
         ["[0, 2, 4]", "[2, 4, 6]", "[0, 1, 2]", "[1, 2, 3]"]),
        ("Коллекции", 3, "Какой тип нельзя использовать как ключ словаря?",
         ["list", "tuple", "str", "frozenset"]),
        ("ООП", 2, "Как называется метод-конструктор класса?",
         ["__init__", "__new__", "__call__", "__create__"]),
        ("ООП", 3, "Что делает функция super() внутри метода класса?",
         ["Даёт доступ к методам родительского класса", "Создаёт новый экземпляр",
          "Удаляет объект", "Делает метод статическим"]),
        ("Исключения", 2, "Какой блок выполняется всегда, была ошибка или нет?",
         ["finally", "else", "except", "with"]),
        ("Исключения", 2, "Какое исключение возникнет при int('abc')?",
         ["ValueError", "TypeError", "KeyError", "NameError"]),
        ("Django", 2, "Какой файл Django-проекта содержит маршруты URL?",
         ["urls.py", "views.py", "models.py", "apps.py"]),
        ("Django", 3, "Что защищает тег {% csrf_token %} в форме?",
         ["От подделки межсайтовых запросов", "От SQL-инъекций",
          "От перебора паролей", "От XSS в шаблонах"]),
    ],
    "Высшая математика": [
        ("Производная", 1, "Производная функции $f(x) = x^3$ равна",
         ["$3x^2$", "$x^2$", "$3x^3$", "$\\frac{x^4}{4}$"]),
        ("Производная", 2, "Найдите $\\left(\\sin x\\right)'$",
         ["$\\cos x$", "$-\\cos x$", "$\\sin x$", "$-\\sin x$"]),
        ("Производная", 2, "Производная $e^{2x}$ равна",
         ["$2e^{2x}$", "$e^{2x}$", "$2xe^{2x-1}$", "$\\frac{e^{2x}}{2}$"]),
        ("Интеграл", 2, "Вычислите $\\int_{0}^{1} 2x\\,dx$", ["$1$", "$2$", "$\\frac{1}{2}$", "$0$"]),
        ("Интеграл", 3, "Первообразная функции $\\frac{1}{x}$ — это",
         ["$\\ln|x| + C$", "$-\\frac{1}{x^2} + C$", "$x\\ln x + C$", "$e^x + C$"]),
        ("Пределы", 2, "Чему равен $\\lim_{x \\to 0} \\frac{\\sin x}{x}$?",
         ["$1$", "$0$", "$\\infty$", "Не существует"]),
        ("Пределы", 3, "Чему равен $\\lim_{n \\to \\infty} \\left(1 + \\frac{1}{n}\\right)^n$?",
         ["$e$", "$1$", "$\\infty$", "$0$"]),
        ("Матрицы", 2, "Определитель $\\begin{vmatrix} 2 & 1 \\\\ 3 & 4 \\end{vmatrix}$ равен",
         ["$5$", "$11$", "$-5$", "$8$"]),
    ],
    "Физика": [
        ("Механика", 1, "Второй закон Ньютона записывается как",
         ["$\\vec{F} = m\\vec{a}$", "$F = mv$", "$E = mc^2$", "$p = \\frac{F}{S}$"]),
        ("Механика", 2, "Кинетическая энергия тела массой $m$ со скоростью $v$ равна",
         ["$\\frac{mv^2}{2}$", "$mv^2$", "$mgh$", "$\\frac{mv}{2}$"]),
        ("Электричество", 2, "Закон Ома для участка цепи:",
         ["$I = \\frac{U}{R}$", "$I = UR$", "$U = \\frac{I}{R}$", "$R = UI$"]),
        ("Электричество", 3, "Сопротивление двух резисторов $R$, соединённых параллельно, равно",
         ["$\\frac{R}{2}$", "$2R$", "$R$", "$R^2$"]),
    ],
}


def demo_iin(prefix: str, serial: int) -> str:
    """Подбирает действительный ИИН с заданной датой рождения."""
    while True:
        try:
            return make_valid_iin(f"{prefix}{serial:04d}")
        except IINError:
            serial += 1


class Command(BaseCommand):
    help = "Заполняет базу демонстрационными данными"

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(iin=demo_iin("8001013", 1)).exists():
            self.stdout.write("Демо-данные уже есть.")
            return
        admin = User.objects.create_superuser(
            demo_iin("8001013", 1), DEMO_PASSWORD, first_name="Админ", last_name="Системный")
        teacher = User.objects.create_user(
            demo_iin("6203154", 7), DEMO_PASSWORD, first_name="Гульнар",
            last_name="Сейтказиева", patronymic="Абдрахмановна", role=User.Role.TEACHER)
        teacher2 = User.objects.create_user(
            demo_iin("5811093", 3), DEMO_PASSWORD, first_name="Марат", last_name="Жунусов",
            patronymic="Ерланович", role=User.Role.TEACHER)
        names = [("Айдана", "Нурланова"), ("Ержан", "Касымов"), ("Динара", "Ахметова"),
                 ("Тимур", "Беков"), ("Алия", "Серикова"), ("Нурсултан", "Оспанов"),
                 ("Жанель", "Мухтарова"), ("Дамир", "Абилов"), ("Камила", "Есенова"),
                 ("Санжар", "Тулегенов"), ("Аружан", "Каримова"), ("Мадияр", "Жаксылыков")]
        students = []
        for i, (first, last) in enumerate(names):
            century = "6" if first.endswith("а") or first in ("Жанель", "Аружан") else "5"
            iin = demo_iin(f"04{(i % 12) + 1:02d}{(i % 27) + 1:02d}{century}", 100 + i)
            students.append(User.objects.create_user(
                iin, DEMO_PASSWORD, first_name=first, last_name=last,
                group="ИС-21" if i % 2 == 0 else "ИС-22"))

        subjects = {}
        for name, rows in QUESTIONS.items():
            subjects[name] = Subject.objects.create(name=name)
            author = teacher if name != "Физика" else teacher2
            for topic, difficulty, text, choices in rows:
                services.save_question(
                    subject=subjects[name], author=author, text=text, topic=topic,
                    difficulty=difficulty,
                    choices=[(c, i == 0) for i, c in enumerate(choices)])

        now = timezone.now()
        open_exam = Exam.objects.create(
            title="Рубежный контроль 1: основы Python", subject=subjects["Программирование на Python"],
            author=teacher, question_count=8, duration_minutes=20, opens_at=now - timedelta(hours=1),
            closes_at=now + timedelta(days=7), access_code="ADAL24", max_violations=3,
            show_review=True)
        past_exam = Exam.objects.create(
            title="Модуль: дифференцирование и интегралы", subject=subjects["Высшая математика"],
            author=teacher, question_count=6, duration_minutes=15,
            opens_at=now - timedelta(days=3), closes_at=now - timedelta(days=2),
            access_code="MATH1", max_violations=3)
        Exam.objects.create(
            title="Итоговый тест по механике", subject=subjects["Физика"], author=teacher2,
            question_count=4, duration_minutes=10, opens_at=now + timedelta(days=2),
            closes_at=now + timedelta(days=2, hours=2), access_code="PHYS")

        self._simulate(past_exam, students, now - timedelta(days=3) + timedelta(minutes=5))
        self._simulate(open_exam, students[4:], now - timedelta(minutes=50))

        self.stdout.write(self.style.SUCCESS("Готово. Пароль у всех: " + DEMO_PASSWORD))
        self.stdout.write(f"Администратор:  {admin.iin}")
        self.stdout.write(f"Преподаватель:  {teacher.iin} ({teacher.full_name})")
        for s in students[:4]:
            self.stdout.write(f"Студент {s.group}: {s.iin} ({s.full_name}) — тест ещё не проходил")
        self.stdout.write("Код доступа к открытому тесту: ADAL24")

    @staticmethod
    def _simulate(exam, students, start):
        """Имитирует прохождение: сильные студенты отвечают верно чаще."""
        rnd = random.Random(exam.pk)
        for idx, student in enumerate(students):
            skill = 0.35 + 0.5 * (idx / max(1, len(students) - 1))
            attempt = services.start_attempt(exam, student, session_key="demo", ip="10.12.5.20",
                                             now=start)
            for item in attempt.items.select_related("question"):
                choices = list(item.question.choices.all())
                right = next(c for c in choices if c.is_correct)
                pick = right if rnd.random() < skill else rnd.choice(choices)
                item.selected = pick
                item.answered_at = start
                item.save()
            status = Attempt.Status.FINISHED
            if idx == 1:
                kinds = ("tab_hidden", "context_menu", "fullscreen_exit")
                for minute, kind in enumerate(kinds, start=2):
                    services.register_violation(attempt, kind, now=start + timedelta(minutes=minute))
                attempt.refresh_from_db()
                continue
            if idx == 3:
                services.register_violation(attempt, "window_blur", now=start + timedelta(minutes=3))
            services.finish_attempt(attempt, status=status, now=start + timedelta(minutes=12))
