"""Собирает архив расширения для страницы /guard/: python extension/build.py"""
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "adal-guard"
OUT = ROOT.parent / "static" / "guard" / "adal-guard.zip"

version = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))["version"]
OUT.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(SRC.rglob("*")):
        if path.is_file():
            zf.write(path, pathlib.Path("adal-guard") / path.relative_to(SRC))
print(f"{OUT} ({version}, {OUT.stat().st_size // 1024} КБ)")
