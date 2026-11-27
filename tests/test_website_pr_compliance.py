from __future__ import annotations

import json
import re
from pathlib import Path

BANNED_PHRASES = (
    "tránh facebook quét",
    "lách thuật toán",
    "spam bot",
    "seeding ảo",
    "0 đồng tuyệt đối",
)


def test_brand_config_and_pr_content_schema() -> None:
    website = Path(__file__).resolve().parent.parent / "website"
    brand = json.loads((website / "brand.config.json").read_text(encoding="utf-8"))
    assert brand["displayName"]
    assert brand["domain"]

    pr = json.loads((website / "content" / "pr_content.json").read_text(encoding="utf-8"))
    for lang in ("vi", "en", "ja", "de", "ko"):
        block = pr[lang]
        assert len(block["problems"]) == 5
        assert len(block["tabs"]) == 3
        assert len(block["compareRows"]) == 5
        assert block["visionTitle"]

    extra = json.loads((website / "content" / "i18n_extra.json").read_text(encoding="utf-8"))
    for lang in ("ja", "de", "ko"):
        assert extra[lang]["heroTitle"]
        assert extra[lang]["casesTitle"]
        assert extra[lang]["faqTitle"]

    index_html = (website / "index.html").read_text(encoding="utf-8")
    for code in ("en", "vi", "ja", "de", "ko"):
        assert f'hreflang="{code}"' in index_html


def test_website_zero_banned_compliance_phrases() -> None:
    website = Path(__file__).resolve().parent.parent / "website"
    hits: list[str] = []
    for path in sorted(website.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".html", ".json", ".css", ".js"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        for phrase in BANNED_PHRASES:
            if re.search(re.escape(phrase), text):
                hits.append(f"{path.name}: {phrase}")
    assert hits == [], f"Banned compliance phrases found: {hits}"
