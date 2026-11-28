"""ИИ-партнёр Адал: наставник для студента и разбор вопросов для преподавателя.

Модели вызываются через Vercel AI Gateway (OpenAI-совместимый API). На Vercel
авторизация идёт по OIDC-токену функции, локально — по AI_GATEWAY_API_KEY.

Принципы, которые закреплены в коде, а не только в подсказке модели:
- в ИИ не уходят ФИО, ИИН и группа: только текст вопроса, варианты и выбор студента;
- наставник не называет правильный ответ: ответ модели проверяется на сервере,
  и если в нём есть текст верного варианта, который студент сам ещё не назвал,
  ответ заменяется наводящим вопросом;
- наставник открывается только после закрытия теста, чтобы разбор не помог тем,
  кто ещё пишет;
- для преподавателя ИИ только советует, вопрос он правит сам.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from django.utils.translation import get_language

logger = logging.getLogger(__name__)

GATEWAY_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
# Основная модель и запасная: если первая недоступна, пробуем вторую.
DEFAULT_MODELS = ("anthropic/claude-haiku-5.5", "google/gemini-3-flash")
TIMEOUT = 25
LETTERS = "АБВГДЕЖЗ"
SOLVED_MARK = "[[SOLVED]]"
MAX_STUDENT_TURNS = 8

LANG_NAMES = {"ru": "Russian", "kk": "Kazakh", "en": "English"}


class AIUnavailable(Exception):
    """Нет ключа, кончились кредиты или модель не ответила."""


def _token(request=None) -> str | None:
    if request is not None:
        token = request.headers.get("x-vercel-oidc-token")
        if token:
            return token
    return os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("VERCEL_OIDC_TOKEN")


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
# Основная модель, затем псевдонимы Google на актуальные Flash и Flash-Lite.
GEMINI_MODELS = ("gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest")
GEMINI_DEFAULT_MODEL = GEMINI_MODELS[0]


def _providers(request=None) -> list[tuple[str, str, str, str]]:
    """Куда идти за ответом, по порядку: (имя, адрес, ключ, модель).
    Сначала Vercel AI Gateway, затем Google Gemini напрямую, если задан GEMINI_API_KEY
    (бесплатный ключ из Google AI Studio, без карты)."""
    providers = []
    token = _token(request)
    if token:
        configured = os.environ.get("AI_GATEWAY_MODEL", "").strip()
        for model in ((configured,) if configured else DEFAULT_MODELS):
            providers.append(("gateway", GATEWAY_URL, token, model))
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if gemini_key:
        configured = os.environ.get("GEMINI_MODEL", "").strip()
        for model in ((configured,) if configured else GEMINI_MODELS):
            providers.append(("gemini", GEMINI_URL, gemini_key, model))
    return providers


def is_enabled(request=None) -> bool:
    return bool(_providers(request))


def chat(messages: list[dict], request=None, max_tokens: int = 450,
         temperature: float = 0.4, timeout: int = TIMEOUT, json_mode: bool = False) -> tuple[str, str]:
    """Возвращает (текст ответа, модель, которая ответила)."""
    providers = _providers(request)
    if not providers:
        logger.warning("AI: no credentials (no OIDC header, AI_GATEWAY_API_KEY, VERCEL_OIDC_TOKEN or GEMINI_API_KEY)")
        raise AIUnavailable("no credentials")
    errors: list[str] = []
    refused: set[str] = set()
    for name, url, token, model in providers:
        if name in refused:
            continue
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        body = json.dumps(payload).encode()
        for attempt in range(2):  # перегрузку (429/503) переживаем одним повтором
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            retry = False
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.load(resp)
                text = (data["choices"][0]["message"]["content"] or "").strip()
                if text:
                    return text, data.get("model") or model
                error = f"{model}: empty reply"
            except urllib.error.HTTPError as exc:
                # Текст ошибки провайдера (без ключа) объясняет причину: нет кредитов, модель недоступна.
                try:
                    detail = json.loads(exc.read().decode("utf-8", "replace"))
                    detail = detail[0] if isinstance(detail, list) and detail else detail
                    detail = detail.get("error", {}) if isinstance(detail, dict) else {}
                    detail = detail.get("message", "") if isinstance(detail, dict) else str(detail)
                except (ValueError, OSError, AttributeError):
                    detail = ""
                error = f"{model}: HTTP {exc.code} {detail[:160]}".strip()
                if exc.code in (401, 402, 403):
                    refused.add(name)  # другие модели этого провайдера ответят так же
                retry = exc.code in (429, 500, 503) and attempt == 0
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
                error = f"{model}: {exc.__class__.__name__}"
            logger.warning("AI %s: %s", name, error)
            if not retry:
                break
            time.sleep(1.5)
        errors.append(error)
    raise AIUnavailable("; ".join(errors))


def _lang() -> str:
    return LANG_NAMES.get((get_language() or "ru")[:2], "Russian")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


# ---------- Наставник для студента ----------

TUTOR_RULES = """You are "Adal mentor", a Socratic tutor inside an exam platform for university students.
The student has already finished the exam. Help them understand the topic behind ONE question they got wrong,
so that they find the right reasoning themselves.

Hard rules:
- Never reveal which option is correct: do not quote it, do not give its letter, do not say "the answer is".
- If the student asks for the answer directly, kindly refuse and give a smaller next step instead.
- Ask exactly one short guiding question per message, building on what the student wrote.
- Point at the concept, a counter-example or a quick self-check, not at the option.
- When the student names the correct option AND explains why, confirm it, sum up the idea in two sentences
  and end your message with the marker [[SOLVED]].
- At most 90 words. Plain text, formulas in LaTeX between single dollar signs, like $x^2$. Reply in {lang}.
- You know nothing about the student personally and must not ask for personal data."""


def question_context(question_text: str, options: list[str], picked: int | None,
                     correct: int) -> str:
    lines = [f"Question: {question_text}", "Options:"]
    lines += [f"{LETTERS[i]}) {text}" for i, text in enumerate(options)]
    lines.append("Student picked: " + (f"{LETTERS[picked]}) {options[picked]}" if picked is not None
                                       else "nothing (no answer)"))
    lines.append(f"Correct option (secret, never reveal): {LETTERS[correct]})")
    return "\n".join(lines)


def leaks_answer(reply: str, correct_text: str, question_text: str, student_text: str) -> bool:
    """В ответе есть текст верного варианта, а студент сам его ещё не назвал."""
    key = _norm(correct_text)
    if len(key) < 2 or key in _norm(question_text):
        return False
    if key in _norm(student_text):
        return False
    return re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", _norm(reply)) is not None


SAFE_FALLBACK = {
    "Russian": "Давайте не спешить с ответом. Какое правило или определение здесь проверяется? Сформулируйте его своими словами.",
    "Kazakh": "Жауапқа асықпайық. Бұл сұрақ қандай ережені немесе анықтаманы тексереді? Оны өз сөзіңізбен айтып көріңіз.",
    "English": "Let's not rush to the answer. Which rule or definition is this question checking? Put it in your own words.",
}


def tutor_reply(*, question_text: str, options: list[str], picked: int | None, correct: int,
                history: list[dict], request=None) -> tuple[str, bool, str]:
    """history — реплики [{"role": "user"|"assistant", "content": str}].
    Возвращает (ответ наставника, разобрался ли студент, модель)."""
    lang = _lang()
    messages = [
        {"role": "system", "content": TUTOR_RULES.format(lang=lang)},
        {"role": "system", "content": question_context(question_text, options, picked, correct)},
    ]
    if not history:
        messages.append({"role": "user", "content": "(The student opened the review. Start with one "
                         "question about their reasoning for the option they picked.)"})
    messages += history
    student_text = " ".join(m["content"] for m in history if m["role"] == "user")
    correct_text = options[correct]

    reply, model = chat(messages, request=request)
    solved = SOLVED_MARK in reply
    reply = reply.replace(SOLVED_MARK, "").strip()
    if leaks_answer(reply, correct_text, question_text, student_text):
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "system", "content": "Your last message revealed the correct option. "
                         "Rewrite it without naming or quoting any option."})
        reply, model = chat(messages, request=request)
        solved = SOLVED_MARK in reply
        reply = reply.replace(SOLVED_MARK, "").strip()
        if leaks_answer(reply, correct_text, question_text, student_text):
            reply, solved = SAFE_FALLBACK[lang], False
    return reply, solved, model


# ---------- Разбор вопроса для преподавателя ----------

REVIEW_RULES = """You are an assessment-quality expert helping a university teacher improve a multiple-choice question.
You get the question, its options with the answer key and item statistics from classical test theory:
p (difficulty, share of correct answers), D (discrimination, upper 27% minus lower 27%) and how often each option was picked.

Write 3-5 short bullet points in {lang}:
1. The most likely problem (wrong key, ambiguous wording, a cue that gives away the answer, non-working distractors, too easy or too hard).
2. What in the numbers supports it.
3. A concrete fix: a rewritten stem or better distractors.
Be honest about uncertainty: the teacher makes the decision. No greetings, no conclusion paragraph.
Formulas in LaTeX between single dollar signs, like $x^2$."""


def review_question(*, text: str, choices: list[tuple[str, bool, float]], p: float, d: float | None,
                    answered: int, flags: list[str], request=None) -> tuple[str, str]:
    lines = [f"Question: {text}", "Options (key marked, share of students who picked it):"]
    for i, (option, is_correct, share) in enumerate(choices):
        lines.append(f"{LETTERS[i]}) {option}{'  [KEY]' if is_correct else ''}  — {round(share * 100)}%")
    lines.append(f"Answered: {answered}; p = {p}; D = {'n/a' if d is None else d}")
    if flags:
        lines.append("Automatic flags: " + "; ".join(str(f) for f in flags))
    messages = [{"role": "system", "content": REVIEW_RULES.format(lang=_lang())},
                {"role": "user", "content": "\n".join(lines)}]
    return chat(messages, request=request, max_tokens=600, temperature=0.3)


# ---------- Вопросы из силлабуса ----------

GENERATE_RULES = """You help a university teacher draft multiple-choice exam questions from their course syllabus.
The teacher will review and edit every draft before it reaches students, so be useful, not exhaustive.

Requirements for each question:
- Test understanding and application of the syllabus topics, not trivia (no dates, page numbers or the syllabus itself).
- Exactly 4 options, exactly one correct. Wrong options must be plausible and reflect real student misconceptions.
- Options of similar length and grammar; no "all of the above", "none of the above", no hints in wording.
- Mix cognitive levels (Bloom): remember, understand, apply, analyze. Most questions should be understand/apply.
- Cover different topics of the syllabus, roughly in proportion to their weight.
- "source": a short quote (max 25 words) from the syllabus that the question is based on.
- "why": one sentence explaining which misconception the wrong options catch.
- Formulas in LaTeX between single dollar signs, like $x^2$. Code in plain text.
- Write questions, options, topic, source and why in {lang}.

Return JSON only, no prose:
{{"questions": [{{"topic": str, "level": "remember|understand|apply|analyze", "text": str,
  "options": [str, str, str, str], "correct": 0-3, "source": str, "why": str}}]}}"""

LEVEL_DIFFICULTY = {"remember": 1, "understand": 2, "apply": 3, "analyze": 3}


def _parse_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json")
    return json.loads(text[start:end + 1])


def clean_generated(raw: dict, limit: int) -> list[dict]:
    """Оставляет только вопросы правильной формы. Модель могла ошибиться — проверяем сами."""
    result, seen = [], set()
    for q in (raw.get("questions") or []) if isinstance(raw, dict) else []:
        if not isinstance(q, dict):
            continue
        text = str(q.get("text", "")).strip()
        options = [str(o).strip() for o in (q.get("options") or []) if str(o).strip()]
        try:
            correct = int(q.get("correct", -1))
        except (TypeError, ValueError):
            continue
        if not text or len(options) != 4 or not 0 <= correct < 4:
            continue
        if len({_norm(o) for o in options}) != 4 or _norm(text) in seen:
            continue
        seen.add(_norm(text))
        level = str(q.get("level", "understand")).strip().lower()
        if level not in LEVEL_DIFFICULTY:
            level = "understand"
        result.append({
            "topic": str(q.get("topic", "")).strip()[:150], "level": level,
            "difficulty": LEVEL_DIFFICULTY[level], "text": text, "options": options,
            "correct": correct, "source": str(q.get("source", "")).strip()[:400],
            "why": str(q.get("why", "")).strip()[:400],
        })
        if len(result) >= limit:
            break
    return result


def generate_questions(*, syllabus: str, count: int, focus: str = "", lang: str | None = None,
                       request=None) -> tuple[list[dict], str]:
    """Черновики вопросов по тексту силлабуса. Возвращает (вопросы, модель)."""
    lang_name = LANG_NAMES.get((lang or get_language() or "ru")[:2], "Russian")
    task = f"Draft {count} questions."
    if focus:
        task += f" Focus on these topics: {focus[:300]}."
    messages = [{"role": "system", "content": GENERATE_RULES.format(lang=lang_name)},
                {"role": "user", "content": task + "\n\nSYLLABUS:\n" + syllabus}]
    text, model = chat(messages, request=request, max_tokens=min(9000, 650 * count),
                       temperature=0.5, timeout=55, json_mode=True)
    try:
        questions = clean_generated(_parse_json(text), count)
    except ValueError as exc:
        raise AIUnavailable("bad json") from exc
    if not questions:
        raise AIUnavailable("no usable questions")
    return questions, model
