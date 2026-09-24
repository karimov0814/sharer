#!/usr/bin/env python3
"""
dyor.net/posts -> Telegram channel forwarder bot.

Har ishga tushganda:
1. Foydalanuvchi nomidan https://dyor.net saytiga LOGIN qiladi (sessiya/cookie olinadi)
2. https://dyor.net/posts sahifasini o'qiydi (login qilingan holda)
3. Yangi (hali yuborilmagan) "signal" postlarni topadi (RODY AI postlari)
4. Ularni chiroyli formatlab Telegram kanaliga yuboradi
5. Yuborilgan postlarni seen_ids.json faylida saqlaydi (qayta yubormaslik uchun)

MUHIM: Bu skript sizning shaxsiy dyor.net hisobingizga kirish uchun DYOR_EMAIL va
DYOR_PASSWORD environment o'zgaruvchilarini talab qiladi. Bularni hech qachon
kodga yozmang — faqat GitHub Secrets orqali bering.

Login: sayt Symfony frameworkda yozilgan, shuning uchun forma maydonlari
"_username" / "_password" deb ataladi va "_csrf_token" nomli CSRF himoyasi bor.
Bu token har safar login sahifasi ochilganda yangilanadi, shuning uchun avval
GET so'rov bilan sahifani ochib tokenni o'qib olamiz, keyin shu token bilan
POST qilamiz.

Xavfsizlik: sessiya (cookie) ataylab faylga SAQLANMAYDI — har ishga tushganda
yangi login qilinadi. Sabab: cookie'ni git repoga commit qilish xavfli (agar
repo oshkor bo'lib qolsa yoki kimdir kirsa, hisobingizga kirib olishi mumkin).
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

BASE_URL = "https://dyor.net"
LOGIN_URL = f"{BASE_URL}/login"
SOURCE_URL = f"{BASE_URL}/posts"

DYOR_EMAIL = os.environ.get("DYOR_EMAIL", "")
DYOR_PASSWORD = os.environ.get("DYOR_PASSWORD", "")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "")  # masalan: @mening_kanalim yoki -1001234567890

STATE_FILE = Path(__file__).parent / "seen_ids.json"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGIN_POST_HEADERS = {
    **REQUEST_HEADERS,
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": BASE_URL,
    "Referer": LOGIN_URL,
}

# ---------------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------------


def login(session: requests.Session) -> None:
    """
    dyor.net saytiga login qiladi va sessiyani (cookie) `session` ichida saqlaydi.

    Symfony login formasi:
        <input name="_username">
        <input name="_password">
        <input type="hidden" name="_csrf_token" value="...">
    """
    if not DYOR_EMAIL or not DYOR_PASSWORD:
        raise RuntimeError(
            "DYOR_EMAIL yoki DYOR_PASSWORD o'rnatilmagan. "
            "GitHub Secrets'ga qo'shishni unutmang."
        )

    # 1) Login sahifasini ochib, joriy CSRF tokenni olamiz.
    resp = session.get(LOGIN_URL, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    token_input = soup.select_one('input[name="_csrf_token"]')
    csrf_token = token_input.get("value") if token_input else None
    print(f"[debug] login sahifasi ochildi, csrf_token topildimi: {bool(csrf_token)}", file=sys.stderr)

    payload = {
        "_username": DYOR_EMAIL,
        "_password": DYOR_PASSWORD,
    }
    if csrf_token:
        payload["_csrf_token"] = csrf_token

    # 2) Login qilamiz (brauzerga o'xshash Origin/Referer header'lari bilan).
    login_resp = session.post(
        LOGIN_URL, data=payload, headers=LOGIN_POST_HEADERS, timeout=20, allow_redirects=True
    )
    login_resp.raise_for_status()

    # 3) Muvaffaqiyatli bo'lganini tekshiramiz: agar hali ham login sahifasida
    #    turgan bo'lsak (parol maydoni hali mavjud) — demak login xato bo'lgan.
    still_on_login = "/login" in login_resp.url or 'name="_password"' in login_resp.text
    print(
        f"[debug] login javobi: status={login_resp.status_code} "
        f"final_url={login_resp.url} muvaffaqiyatli={not still_on_login}",
        file=sys.stderr,
    )
    if still_on_login:
        # Sababni aniqlashga harakat qilamiz: xato xabari, captcha, yoki boshqa narsa.
        fail_soup = BeautifulSoup(login_resp.text, "html.parser")

        # Odatiy xato xabari elementlari (Symfony/Bootstrap-uslub loyihalarda ko'p uchraydi)
        error_el = fail_soup.select_one(
            ".alert, .alert-danger, .flash, [class*='error'], [role='alert']"
        )
        error_text = _clean_text(error_el.get_text()) if error_el else None

        lower_html = login_resp.text.lower()
        has_captcha = any(
            kw in lower_html for kw in ("captcha", "recaptcha", "hcaptcha", "turnstile", "cf-turnstile")
        )

        print(f"[debug] sahifadagi xato xabari: {error_text!r}", file=sys.stderr)
        print(f"[debug] captcha/bot-tekshiruv izlari bormi: {has_captcha}", file=sys.stderr)
        snippet = login_resp.text[:1200].replace("\n", " ")
        print(f"[debug] login javobi HTML (birinchi 1200 belgi): {snippet}", file=sys.stderr)

        raise RuntimeError(
            "Login muvaffaqiyatsiz bo'ldi. Email/parolni tekshiring, "
            "yoki saytda qo'shimcha tekshiruv (captcha/2FA) talab qilinayotgan bo'lishi mumkin."
        )


def get_authenticated_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    login(session)
    return session


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


def fetch_posts(session: requests.Session) -> list:
    """
    dyor.net/posts sahifasidan (login qilingan sessiya orqali) har bir
    RODY AI signal postini to'liq ma'lumotlar bilan chiqarib oladi.
    """
    resp = session.get(SOURCE_URL, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # --- DIAGNOSTIKA ---
    article_count = len(soup.select("article"))
    print(
        f"[debug] /posts: status={resp.status_code} final_url={resp.url} "
        f"content-length={len(resp.text)} article_soni={article_count}",
        file=sys.stderr,
    )
    if article_count == 0:
        snippet = resp.text[:800].replace("\n", " ")
        print(f"[debug] javob boshi: {snippet}", file=sys.stderr)
    # --- DIAGNOSTIKA TUGADI ---

    posts = []

    for article in soup.select("article"):
        post_id = _extract_post_id(article)

        symbol_span = article.select_one(
            "div.flex.items-center.gap-1\\.5.mb-2.flex-wrap span.font-semibold"
        )
        symbol = _clean_text(symbol_span.get_text()) if symbol_span else None

        pair_span = symbol_span.find_next_sibling("span") if symbol_span else None
        pair = _clean_text(pair_span.get_text()) if pair_span else None

        direction_span = article.select_one('span[style*="background:rgba"]')
        direction = None
        if direction_span:
            raw = _clean_text(direction_span.get_text())
            direction = re.sub(r"^[^\w]+", "", raw).strip()

        timeframe_span = article.select_one("span.font-mono.font-semibold")
        timeframe = _clean_text(timeframe_span.get_text()) if timeframe_span else None

        confidence_span = article.select_one(
            "span.tabular-nums.text-emerald-600, span.tabular-nums.text-emerald-400"
        )
        confidence = _clean_text(confidence_span.get_text()) if confidence_span else None

        text = _extract_full_text(article)

        link_el = article.select_one('a[href^="/coin/"]')
        href = BASE_URL + link_el["href"] if link_el else None

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
    import hashlib

    if post.get("url"):
        return hashlib.sha256(post["url"].encode("utf-8")).hexdigest()

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
        session = get_authenticated_session()
    except (requests.RequestException, RuntimeError) as exc:
        print(f"Login/ulanishda xatolik: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        posts = fetch_posts(session)
    except requests.RequestException as exc:
        print(f"Sahifani olishda xatolik: {exc}", file=sys.stderr)
        sys.exit(1)

    new_count = 0
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
