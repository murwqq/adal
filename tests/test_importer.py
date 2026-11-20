import io

import pytest
from docx import Document
from docx.oxml import parse_xml
from PIL import Image

from exams.importer import (ImportError_, import_file, normalize_image, omml_to_latex,
                            strip_list_marker)

MATH_NS = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def omml(inner: str):
    return parse_xml(f'<m:oMath {MATH_NS}>{inner}</m:oMath>')


def r(text):
    return f"<m:r><m:t>{text}</m:t></m:r>"


def test_text_format_first_variant_is_correct():
    data = ("<topic>Основы\n<question>Сколько будет 2+2?\n<variant>4\n<variant>5\n"
            "<variant>B) 22\n").encode()
    items, errors = import_file("test.txt", data)
    assert errors == []
    q = items[0]
    assert q.text == "Сколько будет 2+2?" and q.topic == "Основы"
    assert q.choices == [("4", True), ("5", False), ("22", False)]


def test_cp1251_text_and_orphan_variant():
    data = "<variant>сирота\n<question>Вопрос\n<variant>да\n<variant>нет".encode("cp1251")
    items, errors = import_file("old.txt", data)
    assert len(items) == 1 and "раньше первого" in errors[0]


def test_empty_file_reports_error():
    items, errors = import_file("e.txt", b"just text")
    assert items == [] and "не найдено" in errors[0]


def test_unsupported_extension():
    with pytest.raises(ImportError_):
        import_file("q.pdf", b"%PDF")


def test_broken_docx():
    with pytest.raises(ImportError_):
        import_file("q.docx", b"not a zip")


@pytest.mark.parametrize("raw,expected", [
    ("A) ответ", "ответ"), ("1. ответ", "ответ"), ("б) ответ", "ответ"),
    ("$x) + 1$", "$x) + 1$"), ("просто", "просто")])
def test_strip_list_marker(raw, expected):
    assert strip_list_marker(raw) == expected


def test_omml_fraction_power_root():
    xml = omml(f"<m:f><m:num>{r('a')}</m:num><m:den>{r('b')}</m:den></m:f>"
               f"<m:sSup><m:e>{r('x')}</m:e><m:sup>{r('2')}</m:sup></m:sSup>"
               f"<m:rad><m:radPr><m:degHide m:val='1'/></m:radPr><m:deg/><m:e>{r('y')}</m:e></m:rad>")
    assert omml_to_latex(xml) == r"\frac{a}{b}{x}^{2}\sqrt{y}"


def test_omml_integral_greek_and_brackets():
    xml = omml(f"<m:nary><m:naryPr><m:chr m:val='∑'/></m:naryPr><m:sub>{r('i=1')}</m:sub>"
               f"<m:sup>{r('n')}</m:sup><m:e>{r('α')}</m:e></m:nary>"
               f"<m:d><m:e>{r('x≤1')}</m:e></m:d>"
               f"<m:sSub><m:e>{r('x')}</m:e><m:sub>{r('0')}</m:sub></m:sSub>")
    latex = omml_to_latex(xml)
    assert r"\sum_{i=1}^{n} \alpha" in latex
    assert r"\left( x\le 1 \right)" in latex
    assert "{x}_{0}" in latex


def test_omml_function_limit_matrix():
    xml = omml(f"<m:func><m:fName>{r('sin')}</m:fName><m:e>{r('x')}</m:e></m:func>"
               f"<m:limLow><m:e>{r('lim')}</m:e><m:lim>{r('x→0')}</m:lim></m:limLow>"
               f"<m:m><m:mr><m:e>{r('1')}</m:e><m:e>{r('2')}</m:e></m:mr></m:m>")
    latex = omml_to_latex(xml)
    assert r"\sin{x}" in latex and r"\lim_{x\to 0}" in latex
    assert r"\begin{matrix}1 & 2\end{matrix}" in latex


def _docx_bytes(build) -> bytes:
    doc = Document()
    build(doc)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_bold_correct_answer_is_reported_and_stripped():
    def build(doc):
        doc.add_paragraph("<question>Столица Казахстана?")
        p = doc.add_paragraph("<variant>")
        p.add_run("Астана").bold = True
        doc.add_paragraph("<variant>Алматы")
        doc.add_paragraph("<variant>Кызылорда")

    items, errors = import_file("q.docx", _docx_bytes(build))
    q = items[0]
    assert q.choices[0] == ("Астана", True)
    assert any("выделен" in w for w in q.warnings)


def test_docx_formula_and_image():
    def build(doc):
        p = doc.add_paragraph("<question>Вычислите ")
        p._p.append(omml(f"<m:f><m:num>{r('1')}</m:num><m:den>{r('2')}</m:den></m:f>"))
        img = io.BytesIO()
        Image.new("RGB", (60, 20), "white").save(img, "PNG")
        img.seek(0)
        doc.add_picture(img)
        doc.add_paragraph("<variant>0,5")
        doc.add_paragraph("<variant>2")

    q = import_file("f.docx", _docx_bytes(build))[0][0]
    assert r"$\frac{1}{2}$" in q.text
    assert q.image is not None
    assert any("мелкая" in w for w in q.warnings)


def test_normalize_image_downscales_and_rejects_garbage():
    big = io.BytesIO()
    Image.new("RGB", (2000, 400), "white").save(big, "JPEG")
    out, warnings = normalize_image(big.getvalue())
    assert Image.open(io.BytesIO(out)).width == 900 and warnings == []
    assert normalize_image(b"garbage")[0] is None
