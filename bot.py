#!/usr/bin/env python3
"""
dyor.net/posts -> Telegram channel forwarder bot.

Har ishga tushganda:
1. https://dyor.net/posts sahifasini o'qiydi
2. Yangi (hali yuborilmagan) "signal" postlarni topadi
3. Ularni Telegram kanaliga yuboradi
4. Yuborilgan postlarni seen_ids.json faylida saqlaydi (qayta yubormaslik uchun)

MUHIM: Quyidagi SELECTOR'larni saytning haqiqiy HTML tuzilishiga qarab
sozlashingiz kerak (brauzerda F12 -> Inspect orqali ko'ring).
"""

import hashlib
import json
import os
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
    # ro'yxatni juda katta bo'lib ketmasligi uchun oxirgi 2000 tasini saqlaymiz
    trimmed = list(seen)[-2000:]
    STATE_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def make_post_id(post: dict) -> str:
    """Post uchun barqaror (o'zgarmas) ID yasaydi, agar saytda alohida ID bo'lmasa."""
    if post.get("url"):
        return hashlib.sha256(post["url"].encode("utf-8")).hexdigest()
    basis = (post.get("text") or "")[:200]
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def fetch_posts() -> list:
    """
    dyor.net/posts sahifasidan postlarni chiqarib oladi.

    !!! BU YERNI SOZLASH KERAK !!!
    Quyidagi selectorlar FAQAT NAMUNA. Saytni Inspect qilib, haqiqiy
    klass/teg nomlarini shu yerga yozing.
    """
    resp = requests.get(SOURCE_URL, headers=REQUEST_HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    posts = []

    # TODO: haqiqiy konteyner selectorini qo'ying, masalan:
    # post_elements = soup.select("div.post-card")
    post_elements = soup.select("[class*='post']")  # <-- vaqtinchalik, aniqlashtiring

    for el in post_elements:
        # TODO: matn, link va (bo'lsa) rasm/sana selectorlarini moslang
        text_el = el.select_one("[class*='text'], p")
        link_el = el.select_one("a[href]")

        text = text_el.get_text(strip=True) if text_el else el.get_text(strip=True)
        href = link_el["href"] if link_el else None
        if href and href.startswith("/"):
            href = "https://dyor.net" + href

        if not text:
            continue

        posts.append({"text": text, "url": href})

    return posts


def send_to_telegram(text: str, url: str | None) -> bool:
    if not BOT_TOKEN or not CHANNEL_ID:
        print("XATOLIK: BOT_TOKEN yoki CHANNEL_ID o'rnatilmagan.", file=sys.stderr)
        return False

    message = text
    if url:
        message += f"\n\n🔗 {url}"

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
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

        sent = send_to_telegram(post["text"], post.get("url"))
        if sent:
            seen.add(pid)
            new_count += 1
            time.sleep(1.5)  # Telegram rate-limit uchun kichik pauza

    save_seen_ids(seen)
    print(f"Tugadi. Jami topilgan post: {len(posts)}, yangi yuborilgan: {new_count}")


if __name__ == "__main__":
    main()
