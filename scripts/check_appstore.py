#!/usr/bin/env python3
"""Check a module against the Odoo Apps vendor guidelines.

Usage: python scripts/check_appstore.py [path/to/module]   (default: payment_recurrente_api)

Exit code 1 if any check FAILS. WARN and INFO lines need a human decision.
Guidelines: https://apps.odoo.com/apps/vendor-guidelines
"""

import ast
import re
import struct
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "payment_recurrente_api"
DESC = MODULE / "static" / "description"

FORBIDDEN_TAGS = {"script", "style", "iframe", "form", "object", "embed", "link", "meta"}
ALLOWED_STYLE_PREFIXES = ("color", "font-", "margin-", "padding-", "border-")
ALLOWED_STYLE_EXACT = {"color", "margin", "padding", "border", "background-color"}
ALLOWED_EXTERNAL = re.compile(
    r"^(mailto:|skype:|https://(www\.)?youtube\.com/watch\?v=|https://teams\.microsoft\.com/)"
)
NON_ENGLISH = re.compile(
    r"[áéíóúñ¿¡ÁÉÍÓÚÑ]|\b(el|la|los|las|para|con|desarrollado|gratuito)\b", re.I
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
results = []


def report(level, message):
    results.append(level)
    print(f"{level:5} {message}")


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.links, self.styles, self.text = [], [], [], []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        attrs = dict(attrs)
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append((tag, key, attrs[key]))
        if attrs.get("style"):
            self.styles.append(attrs["style"])
        if tag in ("style", "script"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.text.append(data.strip())


# --- manifest ---
manifest_path = MODULE / "__manifest__.py"
manifest = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))

name = manifest.get("name", "")
if len(name) <= 25:
    report("PASS", f"name has {len(name)} characters (max 25): {name!r}")
else:
    report("FAIL", f"name has {len(name)} characters, the limit is 25: {name!r}")

version = manifest.get("version", "")
if re.fullmatch(r"\d+\.\d+\.\d+\.\d+\.\d+", version):
    report("PASS", f"version follows <odoo>.<major>.<minor>.<bugfix>: {version}")
    if int(version.split(".")[2]) >= 1:
        report("INFO", "version is >= 1.0: it declares the app complete (beta apps must be < 1.0)")
else:
    report("FAIL", f"version must look like 19.0.1.0.0, got {version!r}")

if manifest.get("license") in {"LGPL-3", "OPL-1", "GPL-3", "AGPL-3", "MIT"}:
    report("PASS", f"license is set: {manifest['license']}")
else:
    report("FAIL", "license is missing or unusual")

report("PASS" if manifest.get("depends") else "FAIL", f"depends: {manifest.get('depends')}")
report("PASS" if manifest.get("summary") else "WARN", "summary is set")
report(
    "PASS" if "price" not in manifest else "INFO",
    "no price: the app is free" if "price" not in manifest else "price is set",
)
if manifest.get("images"):
    missing = [i for i in manifest["images"] if not (MODULE / i).exists()]
    report("FAIL" if missing else "PASS", f"cover image (images key) files missing: {missing}")
else:
    report("WARN", "no `images` key: no cover image/thumbnail, which lowers the store score")
if (MODULE / "static/description/icon.png").exists():
    icon = (MODULE / "static/description/icon.png").read_bytes()
    width, height = struct.unpack(">II", icon[16:24])
    ok = width == height
    report(
        "PASS" if ok else "FAIL",
        f"icon.png is {width}x{height}" + ("" if ok else " (must be square)"),
    )
else:
    report("FAIL", "static/description/icon.png is missing (no icon lowers the store score)")

# --- description page ---
page_path = DESC / "index.html"
if not page_path.exists():
    report("FAIL", "static/description/index.html is missing (HTML description scores higher)")
    sys.exit(1)

page = Page()
page.feed(page_path.read_text(encoding="utf-8"))

bad_tags = sorted({t for t in page.tags if t in FORBIDDEN_TAGS})
report(
    "FAIL" if bad_tags else "PASS",
    f"forbidden tags in the description: {bad_tags}"
    if bad_tags
    else "no script/style/iframe/form tags",
)

bad_links = []
for _tag, _key, url in page.links:
    if re.match(r"^[a-z][a-z0-9+.-]*:|^//", url, re.I):
        if not ALLOWED_EXTERNAL.match(url):
            bad_links.append(url)
    elif not url.startswith("#") and not (DESC / url).exists():
        bad_links.append(f"{url} (file not found in static/description)")
report(
    "FAIL" if bad_links else "PASS",
    f"links not allowed or broken: {sorted(set(bad_links))}" if bad_links else "only allowed links",
)

bad_props = set()
for style in page.styles:
    for declaration in style.split(";"):
        prop = declaration.split(":")[0].strip().lower()
        allowed = prop in ALLOWED_STYLE_EXACT or prop.startswith(ALLOWED_STYLE_PREFIXES)
        if prop and not allowed:
            bad_props.add(prop)
report(
    "WARN" if bad_props else "PASS",
    f"inline style properties outside color/font-*/margin-*/padding-*/border-*: {sorted(bad_props)}"
    if bad_props
    else "inline styles use only allowed properties",
)

classes = re.findall(r'class="([^"]+)"', page_path.read_text(encoding="utf-8"))
oe_classes = sorted({c for cl in classes for c in cl.split() if c.startswith("oe_")})
if oe_classes:
    report(
        "INFO",
        f"legacy oe_* classes found (the guideline asks for Bootstrap 4 classes): {oe_classes}",
    )

english = " ".join(page.text)
hits = sorted(set(m.group(0).lower() for m in NON_ENGLISH.finditer(english)))
report(
    "WARN" if hits else "PASS",
    f"the description must be in English; possible Spanish words: {hits[:8]}"
    if hits
    else "the description looks English",
)

for word in ("recurrente account", "external service"):
    if word in english.lower():
        report("PASS", "the description says it needs an external Recurrente account")
        break
else:
    report("WARN", "the description should say it needs a Recurrente account (external service)")
report(
    "PASS" if re.search(r"data (is |are )?sent|sends?\b.*\brecurrente", english, re.I) else "WARN",
    "the description explains which data is sent to Recurrente",
)

print()
print(f"{results.count('PASS')} PASS, {results.count('WARN')} WARN, {results.count('FAIL')} FAIL")
sys.exit(1 if "FAIL" in results else 0)
