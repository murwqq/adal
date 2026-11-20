"""Импорт вопросов из Word (.docx) или текста в формате Platonus.

    <question>Текст вопроса (можно с формулой из редактора формул Word)
    <variant>Правильный ответ — всегда первый
    <variant>Неверный ответ
    <variant>...

Необязательно: строка <topic>Название темы — относится ко всем следующим вопросам.

Что делает импорт ради честности теста:
* выбрасывает всё оформление (жирный, курсив, цвет, размер) — по оформлению
  больше нельзя угадать правильный вариант;
* формулы из редактора формул Word переводит в LaTeX, и они рисуются чётко;
* картинки приводит к одному формату (PNG, ограниченная ширина) и
  предупреждает о слишком мелких, нечитаемых изображениях;
* убирает нумерацию «A)», «1.» в начале вариантов — порядок всё равно перемешивается.
"""
from django.utils.translation import gettext_lazy as _
import io
import re
import zipfile
from dataclasses import dataclass, field

from PIL import Image, UnidentifiedImageError

from .analysis import lint_question

QUESTION_TAG = re.compile(r"<\s*question\s*\d*\s*>", re.I)
VARIANT_TAG = re.compile(r"<\s*variant\s*>", re.I)
TOPIC_TAG = re.compile(r"<\s*topic\s*>", re.I)
TAG_SPLIT = re.compile(r"(<\s*(?:question\s*\d*|variant|topic)\s*>)", re.I)
LIST_MARKER = re.compile(r"^\s*(?:[A-Za-zА-Яа-яЁё]|\d{1,2})\s*[).]\s+")
MAX_IMAGE_WIDTH = 900
MIN_READABLE_WIDTH = 220
MIN_READABLE_HEIGHT = 40

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
V = "{urn:schemas-microsoft-com:vml}"

SYMBOLS = {
    "×": r"\times ", "·": r"\cdot ", "÷": r"\div ", "±": r"\pm ", "∓": r"\mp ",
    "≤": r"\le ", "≥": r"\ge ", "≠": r"\ne ", "≈": r"\approx ", "∞": r"\infty ",
    "→": r"\to ", "∈": r"\in ", "∉": r"\notin ", "∪": r"\cup ", "∩": r"\cap ",
    "⊂": r"\subset ", "∅": r"\emptyset ", "∀": r"\forall ", "∃": r"\exists ",
    "∂": r"\partial ", "∇": r"\nabla ", "√": r"\sqrt{}", "°": r"^\circ ", "−": "-",
    "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ", "Δ": r"\Delta ",
    "ε": r"\varepsilon ", "θ": r"\theta ", "λ": r"\lambda ", "μ": r"\mu ", "π": r"\pi ",
    "ρ": r"\rho ", "σ": r"\sigma ", "Σ": r"\Sigma ", "τ": r"\tau ", "φ": r"\varphi ",
    "ω": r"\omega ", "Ω": r"\Omega ",
}
NARY = {"∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∬": r"\iint", "∮": r"\oint"}
ACCENTS = {"̂": r"\hat", "⃗": r"\vec", "̅": r"\bar", "̇": r"\dot", "̃": r"\tilde"}
FUNCTIONS = {"sin", "cos", "tg", "tan", "ctg", "cot", "ln", "lg", "log", "exp", "lim",
             "arcsin", "arccos", "arctg", "max", "min"}
KNOWN_TEX_FUNCS = {"sin", "cos", "tan", "cot", "ln", "lg", "log", "exp", "lim",
                   "arcsin", "arccos", "max", "min"}


class ImportError_(Exception):
    """Файл нельзя прочитать как документ с вопросами."""


@dataclass
class Segment:
    """Кусок документа: абзац текста (уже без оформления) и картинки в нём."""
    text: str
    images: list[bytes] = field(default_factory=list)
    styled: bool = False


@dataclass
class ImportedQuestion:
    text: str
    topic: str = ""
    choices: list[tuple[str, bool]] = field(default_factory=list)
    image: bytes | None = None
    warnings: list[str] = field(default_factory=list)
    styled_choices: list[bool] = field(default_factory=list)


# ---------- Формулы Word (OMML) → LaTeX ----------

def _sym(text: str) -> str:
    return "".join(SYMBOLS.get(ch, ch) for ch in text)


def _child(el, name):
    return el.find(M + name)


def _attr_val(el, path):
    node = el.find(path)
    return node.get(M + "val") if node is not None else None


def omml_to_latex(el) -> str:
    """Рекурсивно переводит элемент формулы Word в строку LaTeX."""
    tag = el.tag.replace(M, "")
    if tag.endswith("Pr"):
        return ""
    if tag == "r":
        text = "".join(t.text or "" for t in el.iter(M + "t"))
        return text if text in FUNCTIONS else _sym(text)
    if tag == "f":
        return r"\frac{%s}{%s}" % (_inner(_child(el, "num")), _inner(_child(el, "den")))
    if tag == "sSup":
        return "{%s}^{%s}" % (_inner(_child(el, "e")), _inner(_child(el, "sup")))
    if tag == "sSub":
        return "{%s}_{%s}" % (_inner(_child(el, "e")), _inner(_child(el, "sub")))
    if tag == "sSubSup":
        return "{%s}_{%s}^{%s}" % (_inner(_child(el, "e")), _inner(_child(el, "sub")),
                                   _inner(_child(el, "sup")))
    if tag == "rad":
        deg = _inner(_child(el, "deg"))
        body = _inner(_child(el, "e"))
        return r"\sqrt[%s]{%s}" % (deg, body) if deg else r"\sqrt{%s}" % body
    if tag == "d":
        beg = _attr_val(el, f"{M}dPr/{M}begChr")
        end = _attr_val(el, f"{M}dPr/{M}endChr")
        beg = "(" if beg is None else beg
        end = ")" if end is None else end
        parts = [_inner(e) for e in el.findall(M + "e")]
        left = {"{": r"\{", "": ".", "|": "|", "[": "["}.get(beg, beg)
        right = {"}": r"\}", "": ".", "|": "|", "]": "]"}.get(end, end)
        return r"\left%s %s \right%s" % (left, ", ".join(parts), right)
    if tag == "nary":
        op = NARY.get(_attr_val(el, f"{M}naryPr/{M}chr") or "∫", r"\int")
        sub, sup = _inner(_child(el, "sub")), _inner(_child(el, "sup"))
        lims = (f"_{{{sub}}}" if sub else "") + (f"^{{{sup}}}" if sup else "")
        return f"{op}{lims} {_inner(_child(el, 'e'))}"
    if tag == "func":
        name = _inner(_child(el, "fName")).strip()
        prefix = "\\" + name if name in KNOWN_TEX_FUNCS else r"\operatorname{%s}" % name
        if name.startswith("\\") or "_" in name:
            prefix = name
        return f"{prefix}{{{_inner(_child(el, 'e'))}}}"
    if tag == "limLow":
        base = _inner(_child(el, "e")).strip()
        base = "\\" + base if base in KNOWN_TEX_FUNCS else base
        return f"{base}_{{{_inner(_child(el, 'lim'))}}}"
    if tag == "limUpp":
        return r"\overset{%s}{%s}" % (_inner(_child(el, "lim")), _inner(_child(el, "e")))
    if tag == "acc":
        cmd = ACCENTS.get(_attr_val(el, f"{M}accPr/{M}chr") or "̂", r"\hat")
        return f"{cmd}{{{_inner(_child(el, 'e'))}}}"
    if tag == "bar":
        return r"\overline{%s}" % _inner(_child(el, "e"))
    if tag == "m":
        rows = [" & ".join(_inner(e) for e in mr.findall(M + "e")) for mr in el.findall(M + "mr")]
        return r"\begin{matrix}%s\end{matrix}" % r" \\ ".join(rows)
    if tag == "eqArr":
        return r"\begin{aligned}%s\end{aligned}" % r" \\ ".join(
            _inner(e) for e in el.findall(M + "e"))
    return _inner(el)


def _inner(el) -> str:
    if el is None:
        return ""
    return "".join(omml_to_latex(child) for child in el)


# ---------- Чтение .docx ----------

def _run_is_styled(run) -> bool:
    """Есть ли у фрагмента текста выделение, которое могло подсказать ответ."""
    rpr = run.find(W + "rPr")
    if rpr is None:
        return False
    for name in ("b", "i", "u", "highlight", "shd"):
        node = rpr.find(W + name)
        if node is not None and node.get(W + "val") not in ("0", "false", "none"):
            return True
    color = rpr.find(W + "color")
    return color is not None and color.get(W + "val") not in (None, "auto", "000000")


def _images_in(node, part) -> list[bytes]:
    blobs = []
    rel_ids = [b.get(R + "embed") for b in node.iter(A + "blip")]
    rel_ids += [d.get(R + "id") for d in node.iter(V + "imagedata")]
    for rid in filter(None, rel_ids):
        rel = part.rels.get(rid)
        if rel is not None and not rel.is_external:
            blobs.append(rel.target_part.blob)
    return blobs


def _paragraph_segment(p, part) -> Segment:
    pieces, styled = [], False
    for node in p.iter():
        if node.tag == W + "t" and node.text:
            pieces.append(node.text)
        elif node.tag in (W + "tab", W + "br"):
            pieces.append(" ")
        elif node.tag == W + "r" and node.find(W + "t") is not None:
            styled = styled or _run_is_styled(node)
        elif node.tag == M + "oMath" and node.getparent().tag != M + "oMathPara":
            pieces.append(" $" + omml_to_latex(node).strip() + "$ ")
        elif node.tag == M + "oMathPara":
            pieces.append(" $" + omml_to_latex(node).strip() + "$ ")
    # Текст внутри формул попал и в w:t? Нет: у формул свои m:t, их мы не берём выше.
    return Segment(text="".join(pieces), images=_images_in(p, part), styled=styled)


def read_docx(data: bytes) -> list[Segment]:
    import docx  # python-docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        document = docx.Document(io.BytesIO(data))
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ImportError_(_("Файл не похож на документ Word (.docx)")) from exc
    body = document.element.body
    segments = []
    for p in body.iter(W + "p"):
        segments.append(_paragraph_segment(p, document.part))
    return segments


def read_text(data: bytes) -> list[Segment]:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_(_("Не удалось определить кодировку текстового файла"))
    return [Segment(line) for line in text.splitlines()]


# ---------- Разбор структуры ----------

def clean_text(text: str) -> str:
    """Убирает лишние пробелы, неразрывные пробелы и невидимые символы."""
    text = text.replace(" ", " ").replace("​", "").replace("﻿", "")
    return re.sub(r"\s+", " ", text).strip()


def strip_list_marker(text: str) -> str:
    return LIST_MARKER.sub("", text, count=1) if not text.startswith("$") else text


def _tokens(segments: list[Segment]):
    """Разбивает абзацы по тегам: [(тег или None, текст, картинки, выделение)]."""
    for seg in segments:
        for part in TAG_SPLIT.split(seg.text):
            if TAG_SPLIT.fullmatch(part):
                yield part, "", [], seg.styled
            elif part.strip():
                yield None, part, [], seg.styled
        if seg.images:
            # Картинки относятся к тому элементу, на котором закончился абзац.
            yield None, "", seg.images, False


def parse_segments(segments: list[Segment]) -> tuple[list[ImportedQuestion], list[str]]:
    questions: list[ImportedQuestion] = []
    errors: list[str] = []
    topic = ""
    current: ImportedQuestion | None = None
    target = None  # "question" | "variant" | "topic"
    for tag, text, images, styled in _tokens(segments):
        if tag is not None:
            if QUESTION_TAG.fullmatch(tag):
                current = ImportedQuestion(text="", topic=topic)
                questions.append(current)
                target = "question"
            elif VARIANT_TAG.fullmatch(tag):
                if current is None:
                    errors.append(_("<variant> стоит раньше первого <question>, этот вариант пропущен"))
                    target = None
                    continue
                current.choices.append(("", not current.choices))
                current.styled_choices.append(False)
                target = "variant"
            else:
                topic, target = "", "topic"
            continue
        if target == "topic":
            topic = clean_text(topic + " " + text)
            continue
        if current is None:
            continue
        if target == "question":
            current.text = (current.text + " " + text).strip()
            if images and current.image is None:
                current.image = images[0]
        elif target == "variant":
            old, ok = current.choices[-1]
            current.choices[-1] = ((old + " " + text).strip(), ok)
            current.styled_choices[-1] = current.styled_choices[-1] or styled
            if images:
                current.warnings.append(
                    _("Картинки в вариантах ответа не переносятся. Напишите вариант текстом или формулой $…$"))
    return questions, errors


def finalize(questions: list[ImportedQuestion]) -> list[ImportedQuestion]:
    """Чистит текст, нормализует картинки и добавляет предупреждения."""
    for q in questions:
        q.text = clean_text(q.text)
        q.choices = [(strip_list_marker(clean_text(t)), ok) for t, ok in q.choices]
        if any(q.styled_choices):
            if q.styled_choices and q.styled_choices[0] and not all(q.styled_choices):
                q.warnings.append(_("В файле правильный ответ был выделен (жирный, цвет или курсив). Выделение снято, у студентов все варианты будут выглядеть одинаково"))
            else:
                q.warnings.append(_("Оформление вариантов (жирный, курсив, цвет) удалено"))
        if q.image is not None:
            q.image, image_warnings = normalize_image(q.image)
            q.warnings.extend(image_warnings)
        q.warnings.extend(lint_question(q.text, q.choices))
    return questions


def normalize_image(blob: bytes) -> tuple[bytes | None, list[str]]:
    try:
        img = Image.open(io.BytesIO(blob))
        img.load()
    except (UnidentifiedImageError, OSError):
        return None, [_("Картинку не удалось прочитать, возможно, это WMF или EMF. Вставьте её как PNG или наберите формулу в редакторе формул")]
    warnings = []
    if img.width < MIN_READABLE_WIDTH or img.height < MIN_READABLE_HEIGHT:
        warnings.append(_("Картинка слишком мелкая (%(w)s×%(h)s px) и может быть нечитаемой. Если это формула, наберите её через $…$") % {"w": img.width, "h": img.height})
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA")
    if img.width > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / img.width
        img = img.resize((MAX_IMAGE_WIDTH, round(img.height * ratio)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue(), warnings


def import_file(filename: str, data: bytes) -> tuple[list[ImportedQuestion], list[str]]:
    """Точка входа: имя файла и байты → вопросы и общие ошибки."""
    name = filename.lower()
    if name.endswith(".docx"):
        segments = read_docx(data)
    elif name.endswith(".txt"):
        segments = read_text(data)
    else:
        raise ImportError_(_("Поддерживаются файлы .docx и .txt"))
    questions, errors = parse_segments(segments)
    if not questions:
        errors.append(_("В файле не найдено ни одного тега <question>"))
    return finalize(questions), errors
