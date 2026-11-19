"""Капча на Pillow — без внешних сервисов, только Python."""
import io
import random
import secrets

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Без похожих символов (0/O, 1/I/l), чтобы капчу мог прочитать любой человек.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LENGTH = 5
SIZE = (220, 72)
SESSION_KEY = "captcha_answer"


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def _font(size: int):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_png(code: str) -> bytes:
    """Рисует код с шумом и лёгким искажением, возвращает PNG."""
    rnd = random.Random()
    img = Image.new("RGB", SIZE, (246, 247, 249))
    draw = ImageDraw.Draw(img)
    for _ in range(6):
        draw.line(
            [(rnd.randint(0, SIZE[0]), rnd.randint(0, SIZE[1])) for _ in range(2)],
            fill=(150, 160, 180), width=2,
        )
    font = _font(38)
    x = 18
    for ch in code:
        glyph = Image.new("RGBA", (48, 60), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((6, 4), ch, font=font, fill=(27, 36, 51))
        glyph = glyph.rotate(rnd.randint(-22, 22), resample=Image.BICUBIC, expand=False)
        img.paste(glyph, (x, rnd.randint(2, 12)), glyph)
        x += 38
    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def check(expected: str | None, given: str | None) -> bool:
    if not expected or not given:
        return False
    return secrets.compare_digest(expected.upper(), given.strip().upper())
