"""Собирает архивы расширения Адал Guard.

python extension/build.py          — архив для страницы /guard/ (static/guard/adal-guard.zip)
python extension/build.py --store  — архив для Chrome Web Store и Edge Add-ons
                                      (extension/dist/adal-guard-<версия>.zip): без адресов
                                      localhost, файлы лежат в корне архива, как требует магазин.
"""
import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "adal-guard"
LOCAL = ("http://127.0.0.1/*", "http://localhost/*")

manifest = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
version = manifest["version"]
store = "--store" in sys.argv

if store:
    out = ROOT / "dist" / f"adal-guard-{version}.zip"
    for script in manifest["content_scripts"]:
        script["matches"] = [m for m in script["matches"] if m not in LOCAL]
    prefix = pathlib.Path()
else:
    out = ROOT.parent / "static" / "guard" / "adal-guard.zip"
    prefix = pathlib.Path("adal-guard")

out.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(SRC.rglob("*")):
        if not path.is_file():
            continue
        name = prefix / path.relative_to(SRC)
        if path.name == "manifest.json" and store:
            zf.writestr(str(name), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        else:
            zf.write(path, name)
print(f"{out} ({version}, {out.stat().st_size // 1024} KB)")
