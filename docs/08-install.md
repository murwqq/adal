# 8. Установка и развёртывание

## Требования

* Python 3.11 или новее (на Windows при установке отметьте «Add Python to PATH»)
* Git
* Windows 10/11 или Linux

## Локальный запуск

```
git clone <адрес репозитория> adal
cd adal
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # демо-данные: пользователи, вопросы, тесты
python manage.py runserver
```

Откройте http://127.0.0.1:8000. Команда `seed_demo` выводит ИИН демо-пользователей,
пароль у всех — `adal2026`, код доступа открытого теста — `ADAL24`.

Создать администратора вручную: `python manage.py createsuperuser` (логин — ИИН).

Проверка: `python -m pytest` — все тесты должны пройти.

## Переводы

Тексты интерфейса на казахском и английском хранятся в `locale_catalog.py`. После изменения:

```
python manage.py build_translations
```

Команда собирает `locale/kk` и `locale/en` и сообщает, если для какой-то строки нет перевода.
GNU gettext не нужен.

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `DJANGO_SECRET_KEY` | Секретный ключ (обязательно задать на сервере) | ключ для разработки |
| `DJANGO_DEBUG` | `1` — режим разработки, `0` — продакшен | `1` |
| `DJANGO_ALLOWED_HOSTS` | Домены через запятую | `127.0.0.1,localhost,testserver` |

Сгенерировать ключ: `python -c "import secrets; print(secrets.token_urlsafe(50))"`.

## Развёртывание на сервере

1. Установите зависимости и выполните `migrate`, как выше.
2. Задайте переменные: `DJANGO_DEBUG=0`, свой `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS=adal.example.kz`.
3. Соберите статику: `python manage.py collectstatic`.
4. Запустите WSGI-сервер:
   * Windows: `pip install waitress` → `waitress-serve --port=8000 config.wsgi:application`
   * Linux: `pip install gunicorn` → `gunicorn -w 3 -b 127.0.0.1:8000 config.wsgi`
5. Поставьте перед ним nginx с HTTPS: nginx отдаёт `/static/` из `staticfiles/` и `/media/`
   из `media/`, остальное проксирует на порт 8000.
6. Настройте ежедневное копирование базы данных (картинки вопросов и аватарки хранятся в ней же).
7. Для нагрузки больше одной-двух аудиторий одновременно замените SQLite на PostgreSQL:
   в `DATABASES` укажите `django.db.backends.postgresql` и параметры подключения.

## Демо-развёртывание на Vercel

Репозиторий готов к Vercel (`vercel.json`, точка входа `config/wsgi.py`).

1. На vercel.com → **Add New → Project** → импортируйте репозиторий с GitHub.
2. В **Environment Variables** добавьте `DJANGO_SECRET_KEY` (длинная случайная строка, см. выше).
   Без неё приложение не запустится — это защита от подделки сессий.
3. Нажмите **Deploy**.

Ограничения демо-режима: база `deploy/demo.sqlite3` копируется в `/tmp` при холодном старте,
поэтому созданные данные периодически сбрасываются к демо-состоянию, а загруженные картинки не
сохраняются. Для постоянной работы подключите PostgreSQL (например, Neon из Vercel Marketplace)
и задайте параметры подключения в `DATABASES`.

## Работа без интернета

Формулы (KaTeX) и шрифты загружаются с CDN. Если в аудитории нет интернета, скачайте KaTeX
0.16.11 в `static/vendor/katex/`, поменяйте ссылки в `templates/_math.html` и уберите ссылку
на Google Fonts в `templates/base.html` — будут использованы системные шрифты.
