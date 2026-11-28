# Адал Guard в магазинах расширений

Здесь всё, что нужно для карточки в Chrome Web Store и Microsoft Edge Add-ons.

| Что | Где |
|---|---|
| Архив для загрузки | `python extension/build.py --store`, файл `extension/dist/adal-guard-<версия>.zip` |
| Скриншоты 1280×800 | `screenshot-1.png`, `screenshot-2.png`, `screenshot-3.png` |
| Малая промо-плитка 440×280 | `promo-tile-440x280.png` |
| Значок 128×128 | `../adal-guard/icons/128.png` |
| Логотип 300×300 (Edge) | `logo-300x300.png` |
| Политика конфиденциальности | https://adal-murex.vercel.app/guard/privacy/ |
| Сайт расширения | https://adal-murex.vercel.app/guard/ |
| Поддержка | https://github.com/murwqq/adal/issues |

Исходники картинок лежат в `src/`. Чтобы пересобрать их, откройте HTML-файлы в Chrome
в режиме headless с окном 1280×800 (для плитки 440×280).

## Карточка

**Название:** Адал Guard

**Категория:** Образование (Education). **Язык по умолчанию:** русский.

**Краткое описание** (до 132 символов, берётся из manifest.json):
Защита экзамена на платформе Адал: пока идёт тест, браузер открывает только Адал, а ИИ-сервисы и новые вкладки заблокированы.

**Подробное описание (русский):**

Адал Guard превращает обычный Chrome или Edge в браузер для экзамена на платформе Адал (adal-murex.vercel.app).

Расширение включается само, когда студент начинает тест, и выключается, когда тест закончен. Пока идёт экзамен:
• браузер открывает только сайт Адал, остальные страницы показывают заглушку «Идёт тест»;
• ChatGPT, Gemini, Claude, DeepSeek, Copilot, Perplexity и сайты с готовыми ответами заблокированы, а попытка открыть их видна преподавателю;
• новые вкладки и окна закрываются сразу;
• преподаватель видит, если в браузере включены ИИ-помощники или расширение отключили во время теста.

Чего расширение не делает: не работает вне экзамена, не видит экран, камеру и микрофон, не читает историю, пароли и содержимое сайтов, не отправляет данные на свои серверы. Рекламы и аналитики нет. Исходный код открыт: github.com/murwqq/adal.

Для компьютерных классов администратор может установить расширение на все машины через групповые политики Chrome (ExtensionInstallForcelist).

**Подробное описание (English):**

Adal Guard turns a regular Chrome or Edge into an exam browser for the Adal testing platform (adal-murex.vercel.app). It switches on by itself when a student starts a test and off when the test ends. During an exam the browser opens only Adal, AI services such as ChatGPT, Gemini, Claude, DeepSeek, Copilot and Perplexity and answer sites are blocked, new tabs and windows close immediately, and the teacher sees attempts to open AI services, enabled AI extensions and attempts to disable the extension. Outside exams it does nothing. It does not see the screen, camera or microphone, does not read history, passwords or page content, and sends no data to its own servers. No ads, no analytics. Open source: github.com/murwqq/adal.

**Подробное описание (қазақша):**

Адал Guard кәдімгі Chrome немесе Edge браузерін Адал платформасындағы емтихан браузеріне айналдырады. Студент тестті бастағанда өзі қосылады, тест біткенде өшеді. Емтихан кезінде браузер тек Адалды ашады, ChatGPT, Gemini, Claude, DeepSeek және дайын жауаптары бар сайттар бұғатталады, жаңа қойындылар бірден жабылады, ал оқытушы оларды ашуға тырысуды көреді. Емтиханнан тыс кеңейтім ештеңе істемейді, экранды, камераны, тарихты және құпиясөздерді көрмейді.

## Вкладка «Конфиденциальность» (Privacy practices)

**Единственное назначение (Single purpose):**
Lock the browser to the Adal exam site while a student takes an exam on the Adal platform and report exam-rule events (attempts to open AI services or new tabs) to that exam page.

**Обоснование разрешений:**

- **declarativeNetRequest:** During an exam, allow only the Adal site and redirect every other page to a built-in "test in progress" page. Rules are added when the exam starts and removed when it ends.
- **tabs:** Close new tabs and windows opened during an exam and return the student to the exam tab; read the exam tab URL to know which site to allow.
- **webNavigation:** Detect navigation to AI services during an exam so the attempt can be reported to the teacher running the exam.
- **management:** At the start of an exam, check whether AI assistant extensions are enabled and report their names to the exam page. Nothing is changed.
- **storage:** Keep the current exam state (attempt id, deadline) in session storage until the exam ends.
- **alarms:** Switch exam mode off automatically when the exam deadline passes.
- **Content script on adal-murex.vercel.app:** Bridge between the Adal exam page and the extension (start/end of exam, event delivery). It runs only on the Adal site.

**Удалённый код:** No, the extension does not use remote code. All code is in the package.

**Сбор данных:** отметить только «Website content» → нет; «Web history» → нет. По сути расширение не собирает пользовательские данные: события экзамена (домен заблокированного ИИ-сайта, названия ИИ-расширений) передаются только открытой странице экзамена на Адал. Подтвердить три пункта:
- I do not sell or transfer user data to third parties, outside of the approved use cases.
- I do not use or transfer user data for purposes that are unrelated to my item's single purpose.
- I do not use or transfer user data to determine creditworthiness or for lending purposes.

**Политика конфиденциальности:** https://adal-murex.vercel.app/guard/privacy/

## Статус

- Microsoft Edge Add-ons: отправлено на проверку 28.11.2026, издатель Adal Team, видимость Public.
  Для проверяющих на сайте всегда открыт тест «Демо: экзамен с Адал Guard», код `GUARD`.

## Как опубликовать

### Chrome Web Store

1. Откройте https://chrome.google.com/webstore/devconsole и войдите в Google-аккаунт.
2. Примите соглашение разработчика и оплатите разовый регистрационный взнос (5 долларов США). Это делает владелец аккаунта.
3. Подтвердите e-mail для связи в разделе Account.
4. Нажмите **New item** и загрузите `extension/dist/adal-guard-1.0.0.zip`.
5. Во вкладке **Store listing** заполните описание, категорию, язык, загрузите значок, скриншоты и плитку из этой папки.
6. Во вкладке **Privacy** заполните единственное назначение, обоснования разрешений и ответы о данных (текст выше).
7. Во вкладке **Distribution** выберите видимость **Public** или **Unlisted** (только по ссылке), регионы — все.
8. Нажмите **Submit for review**. Проверка обычно занимает от нескольких дней до пары недель; из-за разрешений tabs и management она может быть дольше.

### Microsoft Edge Add-ons (бесплатно)

1. Откройте https://partner.microsoft.com/dashboard/microsoftedge/overview и зарегистрируйтесь как разработчик (бесплатно).
2. **Create new extension**, загрузите тот же zip.
3. Заполните те же поля: описание, категорию, политику конфиденциальности, скриншоты.
4. Отправьте на проверку (обычно до 7 рабочих дней).

После публикации замените на странице /guard/ кнопку «Скачать» ссылкой на карточку в магазине.
