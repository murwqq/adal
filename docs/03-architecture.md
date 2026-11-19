# 3. Описание архитектуры

## 3.1 Назначение и контекст

```mermaid
flowchart LR
  S[Студент<br/>браузер в аудитории] -- HTTPS --> A((Адал))
  T[Преподаватель<br/>браузер] -- HTTPS --> A
  AD[Администратор] -- /admin --> A
  A -- SQL --> DB[(База данных<br/>SQLite / PostgreSQL)]
  A -- файлы --> M[[Папка media<br/>рисунки вопросов]]
  S -. KaTeX, шрифты .-> CDN[CDN jsDelivr,<br/>Google Fonts]
```

## 3.2 Слои и компоненты

Применена трёхуровневая схема из пособия (раздел 2.3): интерфейс → бизнес-логика → хранение.
Бизнес-логика не зависит от HTTP, поэтому её можно вызвать из веб-интерфейса, консоли
(`seed_demo`) или тестов.

```mermaid
flowchart TB
  subgraph UI[Интерфейс]
    V1[accounts/views.py<br/>вход, регистрация, капча]
    V2[exams/views.py<br/>банк, тесты, прохождение, мониторинг]
    TPL[templates/ + static/<br/>HTML, CSS, exam.js]
  end
  subgraph BL[Бизнес-логика — без HTTP]
    SVC[exams/services.py<br/>варианты, таймер, нарушения, поиск]
    AN[exams/analysis.py<br/>трудность, дискриминация, гистограмма]
    IMP[exams/importer.py<br/>.docx/.txt → вопросы, OMML → LaTeX]
    IIN[accounts/iin.py<br/>проверка ИИН]
  end
  subgraph DATA[Хранение]
    ORM[models.py — Django ORM]
    DB[(SQLite)]
  end
  V1 --> IIN
  V2 --> SVC & AN & IMP
  SVC --> AN & ORM
  ORM --> DB
```

`analysis.py`, `importer.py`, `iin.py` — чистые функции без Django: их можно перенести в
настольное или мобильное приложение без изменений.

## 3.3 Схема базы данных

```mermaid
erDiagram
  USER ||--o{ QUESTION : "автор"
  USER ||--o{ EXAM : "автор"
  USER ||--o{ ATTEMPT : "студент"
  SUBJECT ||--o{ QUESTION : ""
  SUBJECT ||--o{ EXAM : ""
  QUESTION ||--|{ CHOICE : "варианты"
  EXAM ||--o{ ATTEMPT : ""
  ATTEMPT ||--|{ ATTEMPT_ITEM : "вопросы варианта"
  ATTEMPT ||--o{ VIOLATION : "нарушения"
  QUESTION ||--o{ ATTEMPT_ITEM : ""
  CHOICE |o--o{ ATTEMPT_ITEM : "выбран"

  USER { string iin UK "12 цифр, логин" string role "student/teacher/admin" string group string password "PBKDF2-хеш" }
  SUBJECT { string name UK }
  QUESTION { text text "простой текст + $LaTeX$" image image int difficulty "1..3" string topic bool is_active }
  CHOICE { string text bool is_correct }
  EXAM { string title int question_count int duration_minutes datetime opens_at datetime closes_at string groups string access_code string allowed_ip_prefix int max_violations }
  ATTEMPT { string status datetime deadline int score int max_score bigint seed string session_key int violation_count }
  ATTEMPT_ITEM { int position json choice_order datetime answered_at }
  VIOLATION { string kind datetime created_at }
```

Ограничения целостности на уровне БД: `UNIQUE(exam, student)` — одна попытка;
`UNIQUE(attempt, position)` — без дублей в варианте; `UNIQUE(iin)`; `UNIQUE(subject.name)`.

## 3.4 Ключевые решения

| Решение | Альтернативы | Почему так |
|---|---|---|
| Django | Flask, FastAPI | В пособии оба допустимы; Django даёт ORM, аутентификацию, CSRF, админку и i18n «из коробки» — меньше своего кода безопасности |
| Таймер на сервере (`deadline` в БД) | Таймер только в браузере | Браузерный таймер можно остановить или подделать. JS лишь показывает время и раз в 10 с сверяется с сервером |
| Запас 5 с после дедлайна (`GRACE_SECONDS`) | Строгий дедлайн | Ответ, отправленный в последнюю секунду, не теряется из-за сетевой задержки |
| Вариант через `random.Random(seed)` | Случайность при каждом запросе | Вариант воспроизводим по `seed` (можно разобрать спорную ситуацию), а порядок хранится в `choice_order` |
| Один вопрос на экране, подгрузка без перезагрузки | Все вопросы на одной странице | Переход на новую страницу сбрасывает полноэкранный режим; в DOM только текущий вопрос — меньше текста для ИИ-расширений |
| Антидребезг нарушений 3 с | Каждое событие отдельно | Переключение вкладки порождает сразу `blur` и `visibilitychange` — это одно действие студента |
| Вопросы хранятся простым текстом + LaTeX | HTML-редактор | Оформление физически невозможно, значит, оно не выдаст ответ |
| Своя функция `py_lower` для SQLite | `icontains` | Встроенные LOWER/LIKE в SQLite не знают регистра кириллицы |
| SQLite | PostgreSQL | Для учебного проекта не нужен отдельный сервер. Ограничение: одна запись в момент времени — на 25–30 студентов аудитории достаточно; при росте — PostgreSQL (одна настройка в `settings.py`) |

## 3.5 Граничный случай: время теста

Дедлайн попытки = `min(started_at + duration, exam.closes_at)`. Студент, начавший за 3 минуты
до закрытия теста, получает 3 минуты, а не всю длительность — это проверяет тест
`test_deadline_limited_by_exam_close`. Ответ принимается до `deadline + 5 с` включительно.

## 3.6 Схема развёртывания

```mermaid
flowchart LR
  subgraph Аудитория
    PC1[ПК студента] & PC2[ПК студента] & PCT[ПК преподавателя]
  end
  PC1 & PC2 & PCT -- HTTPS --> NG[nginx<br/>TLS, статика]
  NG -- HTTP --> WS[waitress / gunicorn<br/>config.wsgi]
  WS --> DJ[Django-приложение]
  DJ --> DB[(SQLite → PostgreSQL)]
  DJ --> MEDIA[[media/]]
```

Подробнее — в [инструкции по установке](08-install.md).
