"""Collect the five ready-made AI daily sites into one normalized JSON, and mirror
their images into this repo so the briefing never depends on the original CDN.

Runs on GitHub Actions (clean direct network). Output:
  sources.json        one object per article: text, links, and image URLs that
                      already point at raw.githubusercontent.com
  img/<date>/<hash>   the mirrored image bytes (pruned after RETENTION_DAYS)

Stdlib only, so the Action needs no `pip install`.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import io
import json
import os
import re
import shutil
import socket
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(TZ)
TODAY = NOW.strftime("%Y-%m-%d")

REPO = os.getenv("REPO", "siweimidu/ai-daily-sources")
BRANCH = os.getenv("BRANCH", "main")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

IMG_DIR = "img"
OUT_FILE = "sources.json"
RETENTION_DAYS = 14
MAX_IMAGES_PER_ITEM = 8
MAX_TOTAL_IMAGES = 120
MAX_IMAGE_BYTES = int(os.getenv("MAX_IMAGE_BYTES", str(2 * 1024 * 1024)))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "30"))

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CTX = ssl.create_default_context()

SOURCES = [
    {
        "name": "juya",
        "label": "Juya AI 早报",
        "type": "rss",
        "url": "https://daily.juya.uk/rss.xml",
        "max_items": 3,
        "window_hours": 48,
    },
    {
        # Feed is ordered oldest-first and timestamps land at 00:00 GMT, so never
        # trust ordering or a tight window here: sort and take the newest N.
        "name": "ainewslog",
        "label": "AI News Log",
        "type": "rss",
        "url": "https://www.ainewslog.com/rss.xml",
        "max_items": 2,
        "window_hours": 48,
        "title_include": r"[\u4e00-\u9fff]",  # skip the English twin of each edition
        "fulltext": True,  # its RSS carries a one-line summary only
    },
    {
        # Publishes a deep-signal weekly digest, not a daily item; newest-one is right.
        "name": "hex2077",
        "label": "何夕2077 AI 深度信号",
        "type": "rss",
        "url": "https://hex2077.dev/rss-zh-CN.xml",
        "max_items": 1,
        "window_hours": 24 * 9,
    },
    {
        "name": "dailydawn",
        "label": "DailyDawn",
        "type": "jsonfeed",
        "url": "https://dailydawn.dev/zh.json",
        "max_items": 2,
        "window_hours": 48,
        "og_image": "https://dailydawn.dev/og/{date}.png",
    },
]

TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
EXT_RE = re.compile(r"\.(png|jpe?g|gif|webp|avif|svg)(?:[?#]|$)", re.I)


def fetch(url: str, timeout: int = 40, tries: int = 3):
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except (urllib.error.URLError, socket.timeout, ET.ParseError, OSError) as exc:
            last = exc
            if attempt < tries - 1:
                import time

                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"fetch failed {url}: {last}")


def strip_html(text: str, limit: int = 700) -> str:
    text = html_mod.unescape(TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_date(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        from email.utils import parsedate_to_datetime

        d = parsedate_to_datetime(raw)
        return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=timezone.utc).astimezone(TZ)
    except Exception:  # noqa: BLE001
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            d = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)
        except ValueError:
            continue
    try:
        d = datetime.fromisoformat(raw)
        return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)
    except ValueError:
        return None


def local(tag: str) -> str:
    return tag.split("}")[-1]


def parse_rss(data: bytes) -> list[dict]:
    root = ET.fromstring(data)
    items = []
    for node in root.iter():
        if local(node.tag) != "item":
            continue
        fields: dict[str, str] = {}
        for child in node:
            key = local(child.tag)
            if key in ("title", "link", "pubDate", "description", "encoded", "creator"):
                fields.setdefault(key, (child.text or "").strip())
        date = parse_date(fields.get("pubDate", ""))
        content = fields.get("encoded") or fields.get("description", "")
        items.append(
            {
                "title": strip_html(fields.get("title", ""), 240),
                "link": fields.get("link", ""),
                "date": date,
                "summary": strip_html(fields.get("description", ""), 500),
                "content_html": content,
            }
        )
    return items


def parse_jsonfeed(data: bytes) -> list[dict]:
    doc = json.loads(data)
    items = []
    for node in doc.get("items", []):
        date = parse_date(node.get("date_published") or node.get("date_modified") or "")
        content = node.get("content_html") or node.get("content_text") or node.get("summary") or ""
        items.append(
            {
                "title": strip_html(node.get("title", ""), 240),
                "link": node.get("url") or node.get("_id") or "",
                "date": date,
                "summary": strip_html(node.get("summary", ""), 500),
                "content_html": content,
                "image": node.get("image") or node.get("banner_image"),
            }
        )
    return items


def pick_items(items: list[dict], src: dict) -> list[dict]:
    """Sort newest-first, keep the freshest `max_items`, preferring ones inside the
    freshness window. Never returns empty while the feed has any dated item, because
    a site that publishes at an odd hour would otherwise silently drop out."""
    window = src.get("window_hours", LOOKBACK_HOURS)
    cap = src.get("max_items", 3)
    pattern = src.get("title_include")

    if pattern:
        kept = [it for it in items if re.search(pattern, it.get("title", ""))]
        items = kept or items

    dated = sorted(
        (it for it in items if it.get("date")),
        key=lambda it: it["date"],
        reverse=True,
    )
    if not dated:
        return items[:cap]

    cutoff = NOW - timedelta(hours=window)
    fresh = [it for it in dated if it["date"] >= cutoff]
    chosen = fresh[:cap] if fresh else dated[:cap]
    if not fresh:
        print(
            f"  ~ {src['name']}: nothing within {window}h, fell back to newest "
            f"({chosen[0]['date'].date()})"
        )
    return chosen


def mime_ext(ctype: str, url: str) -> str:
    ctype = (ctype or "").lower()
    for key, ext in (
        ("png", ".png"), ("jpeg", ".jpg"), ("jpg", ".jpg"), ("gif", ".gif"),
        ("webp", ".webp"), ("avif", ".avif"), ("svg", ".svg"),
    ):
        if key in ctype:
            return ext
    m = EXT_RE.search(url)
    if m:
        e = m.group(1).lower()
        return ".jpg" if e == "jpeg" else f".{e}"
    return ".jpg"


class Mirror:
    def __init__(self) -> None:
        self.map: dict[str, str] = {}
        self.count = 0
        # POSIX separators only: these strings become public raw.githubusercontent
        # URLs, and os.path.join would bake a Windows backslash into them on a local
        # run, 404-ing every image.
        self.dir = f"{IMG_DIR}/{TODAY}"
        os.makedirs(self.dir.replace("/", os.sep), exist_ok=True)

    def mirror(self, url: str) -> str | None:
        url = html_mod.unescape((url or "").strip())
        if not url.startswith("http"):
            return None
        if url in self.map:
            return self.map[url]
        if self.count >= MAX_TOTAL_IMAGES:
            return None
        try:
            data, ctype = fetch(url, timeout=40, tries=2)
        except Exception as exc:  # noqa: BLE001 - one dead image must not stop the run
            print(f"  ! image skip {url[:70]}: {str(exc)[:70]}")
            return None
        if not data or len(data) > MAX_IMAGE_BYTES:
            print(f"  ! image over {MAX_IMAGE_BYTES // 1024}KB, kept remote: {url[:70]}")
            return None
        ext = mime_ext(ctype, url)
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:24] + ext
        with open(os.path.join(self.dir, name).replace("/", os.sep), "wb") as fh:
            fh.write(data)
        self.count += 1
        raw = f"{RAW_BASE}/{self.dir}/{name}"
        self.map[url] = raw
        return raw

    def rewrite(self, content: str, limit: int = MAX_IMAGES_PER_ITEM) -> tuple[str, list[str]]:
        """Swap every <img src> for its mirrored copy; keep the original if the
        mirror failed, so a flaky source CDN never blanks the briefing.

        Mirroring is capped as it goes, so no bytes are downloaded for images the
        item will not actually carry.
        """
        if not content:
            return "", []
        used: list[str] = []
        swapped: dict[str, str] = {}
        state = {"budget": limit}

        def sub(match: re.Match) -> str:
            original = match.group(1)
            key = html_mod.unescape(original)
            if key in swapped:  # repeat image: reuse, does not spend budget
                return match.group(0).replace(original, swapped[key])
            if state["budget"] <= 0:
                return match.group(0)
            state["budget"] -= 1
            mirrored = self.mirror(key)
            if not mirrored:
                return match.group(0)
            swapped[key] = mirrored
            used.append(mirrored)
            return match.group(0).replace(original, mirrored)

        out = IMG_RE.sub(sub, content)
        return out, used


def prune_images() -> int:
    removed = 0
    if not os.path.isdir(IMG_DIR):
        return 0
    keep_after = (NOW - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    for day in os.listdir(IMG_DIR):
        path = os.path.join(IMG_DIR, day)
        if os.path.isdir(path) and day < keep_after:
            shutil.rmtree(path)
            removed += 1
    return removed


def article_body(url: str) -> str:
    """Fetch the item page and return its <article>/<main> body HTML.

    Some feeds ship a one-line summary and keep the real content on the page;
    without this the briefing would carry a headline and nothing to read.
    Returns "" when the page or block cannot be found — never raises.
    """
    if not url.startswith("http"):
        return ""
    try:
        data, _ = fetch(url, timeout=30, tries=2)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! fulltext skip {url[:70]}: {str(exc)[:70]}")
        return ""
    page = data.decode("utf-8", "replace")
    for pattern in (r"<article[^>]*>(.*?)</article>", r"<main[^>]*>(.*?)</main>"):
        m = re.search(pattern, page, re.S | re.I)
        if m and len(m.group(1)) > 400:
            return m.group(1)
    return ""


def main() -> int:
    print(f"=== ai-daily-sources {TODAY} (lookback {LOOKBACK_HOURS}h) ===")
    mirror = Mirror()
    articles: list[dict] = []
    failures: list[str] = []

    for src in SOURCES:
        try:
            data, _ = fetch(src["url"])
            if src["type"] == "rss":
                items = pick_items(parse_rss(data), src)
            else:
                items = pick_items(parse_jsonfeed(data), src)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{src['name']}: {str(exc)[:120]}")
            print(f"✗ {src['name']} fetch failed: {str(exc)[:120]}")
            continue

        for it in items:
            raw_content = it.get("content_html", "")
            if src.get("fulltext") and len(strip_html(raw_content, 99999)) < 600:
                body = article_body(it.get("link", ""))
                if body:
                    raw_content = body
            content, images = mirror.rewrite(raw_content)
            head = it.get("image")
            if head:
                mirrored = mirror.mirror(head)
                if mirrored:
                    images.insert(0, mirrored)
            og = src.get("og_image")
            if og and not images:
                try:
                    og_url = og.format(date=it["date"].strftime("%Y-%m-%d") if it.get("date") else TODAY)
                    mirrored = mirror.mirror(og_url)
                    if mirrored:
                        images.insert(0, mirrored)
                except Exception:  # noqa: BLE001
                    pass
            articles.append(
                {
                    "source": src["name"],
                    "source_label": src["label"],
                    "title": it["title"],
                    "link": it["link"],
                    "published_at": it["date"].isoformat() if it.get("date") else None,
                    "summary": it["summary"],
                    "content_html": content[:120000],
                    "plain_text": strip_html(content, 6000),
                    "images": images[:MAX_IMAGES_PER_ITEM],
                    "cover": images[0] if images else None,
                }
            )
        print(f"✓ {src['name']}: {len(items)} article(s)")

    seen_keys: set[str] = set()
    deduped: list[dict] = []
    for a in articles:
        url_key = re.sub(r"^https?://(www\.)?", "", (a["link"] or "").rstrip("/")).lower()
        title_key = re.sub(r"\W+", "", a["title"].lower())[:60]
        if (url_key and url_key in seen_keys) or (title_key and title_key in seen_keys):
            continue
        seen_keys.update({url_key, title_key})
        deduped.append(a)

    deduped.sort(key=lambda a: a["published_at"] or "", reverse=True)
    removed = prune_images()

    payload = {
        "generated_at": NOW.isoformat(),
        "date": TODAY,
        "count": len(deduped),
        "images_mirrored": mirror.count,
        "sources": [
            {
                "name": s["name"],
                "label": s["label"],
                "feed": s["url"],
                "articles": sum(1 for a in deduped if a["source"] == s["name"]),
            }
            for s in SOURCES
        ],
        "failures": failures,
        "articles": deduped,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(
        f"✓ wrote {OUT_FILE}: {len(deduped)} article(s), {mirror.count} image(s) mirrored, "
        f"{removed} old day-folder(s) pruned"
    )
    if failures:
        print("⚠ failures: " + "; ".join(failures))
    return 0 if deduped else 1


if __name__ == "__main__":
    raise SystemExit(main())
