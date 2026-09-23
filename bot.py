#!/usr/bin/env python3
"""
dyor.net/posts -> Telegram channel forwarder bot.

Har ishga tushganda:
1. https://dyor.net/posts sahifasini o'qiydi
2. Yangi (hali yuborilmagan) "signal" postlarni topadi (RODY AI postlari)
3. Ularni chiroyli formatlab Telegram kanaliga yuboradi
4. Yuborilgan postlarni seen_ids.json faylida saqlaydi (qayta yubormaslik uchun)

Bu versiya saytning haqiqiy HTML tuzilishiga (siz yuborgan namunaga) moslab yozildi:
- Har bir post <article> ichida
- Coin/pair: "UNI" / "USDT"
- Yo'nalish: bullish/bearish belgisi
- Ishonch darajasi: "8.8/10"
- Timeframe: "1d" / "4h"
- Matn: reveal-widget ichida (qisqa qism + "Show more" ostidagi yashirin qism)
- Entry / SL / TP qiymatlari
- Coin sahifasiga link: /coin/<SYMBOL>/<PAIR>
- Postning barqaror ID'si: mini-chart widgetidagi /posts/<ID>/chart-data manzilidan olinadi
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# SOZLAMALAR
# ---------------------------------------------------------------------------

SOURCE_URL = "https://dyor.net/posts"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")  # masalan: @mening_kanalim yoki -1001234567890

STATE_FILE = Path(__file__).parent / "seen_ids.json"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

BASE_URL = "https://dyor.net"

# ---------------------------------------------------------------------------
# YORDAMCHI FUNKSIYALAR
# ---------------------------------------------------------------------------


def load_seen_ids() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen_ids(seen: set) -> None:
    # ro'yxat juda katta bo'lib ketmasligi uchun oxirgi 2000 tasini saqlaymiz
    trimmed = list(seen)[-2000:]
    STATE_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_full_text(article) -> str:
    """
    Post matnini reveal-widgetdan to'liq oladi:
    ko'rinadigan qism + "Show more" ostidagi yashirin qism.
    "..." (ellipsis) belgisini olib tashlaydi.
    """
    p_tag = article.select_one('div[data-controller="reveal"] p')
    if not p_tag:
        return ""

    p_copy = BeautifulSoup(str(p_tag), "html.parser")
    ellipsis = p_copy.select_one('[data-reveal-target="ellipsis"]')
    if ellipsis:
        ellipsis.decompose()

    # hidden content spanini ochiq qismga tabiiy ravishda qo'shib olamiz
    content_span = p_copy.select_one('[data-reveal-target="content"]')
    if content_span:
        del content_span["class"]  # "hidden" klassini olib tashlaymiz (faqat vizual)

    return _clean_text(p_copy.get_text(" ", strip=True))


def _extract_level(article, label: str) -> str | None:
    """Entry / SL / TP kabi darajalarni chiqaradi."""
    for span in article.select("span"):
        label_span = span.select_one("span")
        if label_span and _clean_text(label_span.get_text()) == label:
            full = _clean_text(span.get_text())
            return full[len(label):].strip()
    return None


def _extract_post_id(article) -> str | None:
    chart_div = article.select_one("[data-mini-chart-url-value]")
    if not chart_div:
        return None
    m = re.search(r"/posts/(\d+)/chart-data", chart_div["data-mini-chart-url-value"])
    return m.group(1) if m else None


def fetch_posts() -> list:
    """
    dyor.net/posts sahifasidan har bir RODY AI signal postini
    to'liq ma'lumotlar (coin, yo'nalish, ishonch, narx darajalari, matn, link)
    bilan birga chiqarib oladi.
    """
    resp = requests.get(SOURCE_URL, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # --- DIAGNOSTIKA: nega 0 post topilishi mumkinligini aniqlash uchun ---
    article_count = len(soup.select("article"))
    print(
        f"[debug] status={resp.status_code} content-length={len(resp.text)} "
        f"article_soni={article_count}",
        file=sys.stderr,
    )
    if article_count == 0:
        # Sayt JS orqali render qiladimi yoki bloklayaptimi — tekshirish uchun
        # javobning boshini logga chiqaramiz (maxfiy ma'lumot bo'lmasa kerak).
        snippet = resp.text[:800].replace("\n", " ")
        print(f"[debug] javob boshi: {snippet}", file=sys.stderr)
    # --- DIAGNOSTIKA TUGADI ---

    posts = []

    for article in soup.select("article"):
        post_id = _extract_post_id(article)

        # Coin belgisi (masalan "UNI") va juftlik ("USDT")
        symbol_span = article.select_one(
            "div.flex.items-center.gap-1\\.5.mb-2.flex-wrap span.font-semibold"
        )
        symbol = _clean_text(symbol_span.get_text()) if symbol_span else None

        pair_span = None
        if symbol_span:
            pair_span = symbol_span.find_next_sibling("span")
        pair = _clean_text(pair_span.get_text()) if pair_span else None

        # Yo'nalish: bullish / bearish (▲/▼ belgisini olib tashlaymiz)
        direction_span = article.select_one('span[style*="background:rgba"]')
        direction = None
        if direction_span:
            raw = _clean_text(direction_span.get_text())
            direction = re.sub(r"^[^\w]+", "", raw).strip()

        # Timeframe (masalan "1d", "4h")
        timeframe_span = article.select_one("span.font-mono.font-semibold")
        timeframe = _clean_text(timeframe_span.get_text()) if timeframe_span else None

        # Ishonch darajasi (masalan "8.8/10")
        confidence_span = article.select_one("span.tabular-nums.text-emerald-600, span.tabular-nums.text-emerald-400")
        confidence = _clean_text(confidence_span.get_text()) if confidence_span else None

        # To'liq matn
        text = _extract_full_text(article)

        # Coin sahifasiga link
        link_el = article.select_one('a[href^="/coin/"]')
        href = BASE_URL + link_el["href"] if link_el else None

        # Entry / SL / TP darajalari
        entry = _extract_level(article, "Entry")
        sl = _extract_level(article, "SL")
        tp = _extract_level(article, "TP")

        if not text and not symbol:
            continue

        posts.append(
            {
                "post_id": post_id,
                "symbol": symbol,
                "pair": pair,
                "direction": direction,
                "timeframe": timeframe,
                "confidence": confidence,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "text": text,
                "url": href,
            }
        )

    return posts


def make_post_id(post: dict) -> str:
    """Post uchun barqaror ID: avvalo saytdagi haqiqiy post ID, aks holda link/matn asosida hash."""
    if post.get("post_id"):
        return f"post-{post['post_id']}"
    if post.get("url"):
        import hashlib

        return hashlib.sha256(post["url"].encode("utf-8")).hexdigest()
    import hashlib

    basis = (post.get("text") or "")[:200]
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def format_message(post: dict) -> str:
    """Postni chiroyli Telegram xabariga formatlaydi."""
    direction_emoji = "🟢" if (post.get("direction") or "").lower() == "bullish" else "🔴"

    header_parts = []
    if post.get("symbol"):
        title = post["symbol"]
        if post.get("pair"):
            title += f"/{post['pair']}"
        header_parts.append(f"<b>{title}</b>")
    if post.get("direction"):
        header_parts.append(f"{direction_emoji} {post['direction'].capitalize()}")
    if post.get("timeframe"):
        header_parts.append(f"⏱ {post['timeframe']}")
    if post.get("confidence"):
        header_parts.append(f"🎯 {post['confidence']}")

    lines = [" | ".join(header_parts)] if header_parts else []

    if post.get("text"):
        lines.append("")
        lines.append(post["text"])

    levels = []
    if post.get("entry"):
        levels.append(f"Entry: {post['entry']}")
    if post.get("sl"):
        levels.append(f"SL: {post['sl']}")
    if post.get("tp"):
        levels.append(f"TP: {post['tp']}")
    if levels:
        lines.append("")
        lines.append(" | ".join(levels))

    if post.get("url"):
        lines.append("")
        lines.append(f"🔗 {post['url']}")

    lines.append("")
    lines.append("<i>NFA</i>")

    return "\n".join(lines)


def send_to_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHANNEL_ID:
        print("XATOLIK: BOT_TOKEN yoki CHANNEL_ID o'rnatilmagan.", file=sys.stderr)
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    resp = requests.post(api_url, data=payload, timeout=20)
    if resp.status_code != 200:
        print(f"Telegramga yuborishda xatolik: {resp.status_code} {resp.text}", file=sys.stderr)
        return False
    return True


def main() -> None:
    seen = load_seen_ids()
    try:
        posts = fetch_posts()
    except requests.RequestException as exc:
        print(f"Sahifani olishda xatolik: {exc}", file=sys.stderr)
        sys.exit(1)

    new_count = 0
    # eski postlardan yangilariga tartibda yuborish uchun teskari aylantiramiz
    for post in reversed(posts):
        pid = make_post_id(post)
        if pid in seen:
            continue

        message = format_message(post)
        sent = send_to_telegram(message)
        if sent:
            seen.add(pid)
            new_count += 1
            time.sleep(1.5)  # Telegram rate-limit uchun kichik pauza

    save_seen_ids(seen)
    print(f"Tugadi. Jami topilgan post: {len(posts)}, yangi yuborilgan: {new_count}")


if __name__ == "__main__":
    main()
