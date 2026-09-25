#!/usr/bin/env python3
"""
dyor.net/posts -> Telegram channel forwarder bot.

Har ishga tushganda:
1. Foydalanuvchi nomidan https://dyor.net saytiga LOGIN qiladi (sessiya/cookie olinadi)
2. https://dyor.net/posts sahifasini o'qiydi (login qilingan holda)
3. Yangi (hali yuborilmagan) "signal" postlarni topadi (RODY AI postlari)
4. Admin (ADMIN_CHAT_ID) tomonidan bergan buyruq/tugmalarni tekshiradi va
   filter sozlamalarini (filters.json) shunga qarab yangilaydi
5. Filterlardan o'tgan VA oxirgi 24 soat ichida yaratilgan postlarni chiroyli
   formatlab Telegram kanaliga yuboradi (eski postlar hech qachon yuborilmaydi)
6. Yuborilgan postlarni seen_ids.json faylida saqlaydi (qayta yubormaslik uchun)

MUHIM: Bu skript sizning shaxsiy dyor.net hisobingizga kirish uchun DYOR_EMAIL va
DYOR_PASSWORD environment o'zgaruvchilarini talab qiladi. Bularni hech qachon
kodga yozmang — faqat GitHub Secrets orqali bering.

Login: sayt Symfony frameworkda yozilgan, shuning uchun forma maydonlari
"_username" / "_password" deb ataladi va "_csrf_token" nomli CSRF himoyasi bor.
Bu token har safar login sahifasi ochilganda yangilanadi, shuning uchun avval
GET so'rov bilan sahifani ochib tokenni o'qib olamiz, keyin shu token bilan
POST qilamiz.

Filter boshqaruvi: bot ADMIN_CHAT_ID orqali sizga /filters panelini yuboradi.
U yerdagi tugmalarni bosib, yo'nalish (Bullish/Bearish), timeframe, minimal
ishonch darajasi va coin whitelist rejimini yoqib/o'chirib qo'yasiz. Bot
GitHub Actions orqali davriy (masalan har 10 daqiqada) ishga tushgani uchun,
tugma bosilgandan keyingi o'zgarish darhol emas, ~10 daqiqa ichida kuchga
kiradi.

Xavfsizlik: sessiya (cookie) ataylab faylga SAQLANMAYDI — har ishga tushganda
yangi login qilinadi. Sabab: cookie'ni git repoga commit qilish xavfli (agar
repo oshkor bo'lib qolsa yoki kimdir kirsa, hisobingizga kirib olishi mumkin).
"""

import copy
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

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

# Filter tugmalarini boshqarish uchun sizning shaxsiy Telegram chat ID'ingiz.
# Bo'sh bo'lsa, /filters paneli ishlamaydi (lekin bot oddiy forward rejimida
# ishlashda davom etadi: barcha filterlar standart holatda "yoqilgan" bo'ladi).
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")

STATE_FILE = Path(__file__).parent / "seen_ids.json"
FILTERS_FILE = Path(__file__).parent / "filters.json"

DEFAULT_FILTERS = {
    "directions": {"bullish": True, "bearish": True},
    # Timeframe ro'yxati DINAMIK: sayt qanday qiymatlar chiqarsa (masalan
    # "1H", "4H", "1D"), ular birinchi marta ko'rilganda shu yerga avtomatik
    # qo'shiladi (standart holatda "yoqilgan"). /filters panelida ko'rinadi.
    "timeframes": {},
    "min_confidence": 0,
    "confidence_options": [0, 50, 60, 70, 80, 90],
    "symbol_mode": "all",  # "all" = cheklovsiz, "whitelist" = faqat ro'yxatdagilar
    "symbol_whitelist": [],
    "only_last_24h": True,
    "max_age_hours": 24,
    "last_update_id": 0,
}

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
# HOLAT FAYLLARI: seen_ids.json (yuborilganlar) va filters.json (sozlamalar)
# ---------------------------------------------------------------------------


def load_seen_ids() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def _seen_sort_key(pid: str):
    # "post-1867" -> (1, 1867); hash ko'rinishidagi ID'lar -> (0, 0) (boshida turadi)
    m = re.fullmatch(r"post-(\d+)", pid)
    return (1, int(m.group(1))) if m else (0, 0)


def save_seen_ids(seen: set) -> None:
    # Ro'yxat juda katta bo'lib ketmasligi uchun eng YANGI 2000 tasini saqlaymiz.
    # (Avval set tartibsiz kesilardi -> tasodifiy postlar o'chib, keyinchalik
    # qayta yuborilishi mumkin edi.)
    trimmed = sorted(seen, key=_seen_sort_key)[-2000:]
    STATE_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def load_filters() -> dict:
    filters = copy.deepcopy(DEFAULT_FILTERS)
    if FILTERS_FILE.exists():
        try:
            saved = json.loads(FILTERS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            saved = {}
        filters.update(saved)
        # Ichki lug'atlarni chuqurroq birlashtiramiz, aks holda eski faylda
        # yo'q bo'lgan yangi kalitlar (masalan yangi timeframe) yo'qolib qoladi.
        for key in ("directions", "timeframes"):
            merged = copy.deepcopy(DEFAULT_FILTERS.get(key, {}))
            merged.update(saved.get(key, {}) or {})
            filters[key] = merged
    return filters


def save_filters(filters: dict) -> None:
    FILTERS_FILE.write_text(json.dumps(filters, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# YORDAMCHI FUNKSIYALAR (HTML'dan ma'lumot olish)
# ---------------------------------------------------------------------------


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


def _extract_level(article, label: str) -> Optional[str]:
    """Entry / SL / TP kabi darajalarni chiqaradi."""
    for span in article.select("span"):
        label_span = span.select_one("span")
        if label_span and _clean_text(label_span.get_text()) == label:
            full = _clean_text(span.get_text())
            return full[len(label):].strip()
    return None


def _extract_post_id(article) -> Optional[str]:
    chart_div = article.select_one("[data-mini-chart-url-value]")
    if not chart_div:
        return None
    m = re.search(r"/posts/(\d+)/chart-data", chart_div["data-mini-chart-url-value"])
    return m.group(1) if m else None


def _extract_post_datetime(article) -> Optional[datetime]:
    """
    Post yaratilgan vaqtni aniqlashga harakat qiladi (UTC qaytaradi).

    DIQQAT (TODO): bu funksiya dyor.net'ning haqiqiy sana/vaqt HTML
    belgisini ko'rmasdan, umumiy taxminlar asosida yozilgan. Agar u doim
    None qaytarsa (loglarda "[debug] Post sanasi topilmadi..." ko'rinadi),
    postdagi sana/vaqt qismining HTML kodini yuboring — men aniq moslab
    beraman. Shu vaqtgacha sana aniqlanmagan postlar YUBORILADI (xavfsiz
    tomonga xato qilamiz — ya'ni yangi signal yo'qolib qolmaydi).
    """
    # 1) Standart HTML: <time datetime="2026-01-01T12:00:00Z">
    time_tag = article.select_one("time[datetime]")
    if time_tag and time_tag.get("datetime"):
        raw = time_tag["datetime"].strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    # 2) data-* atributlar orqali timestamp/ISO sana (ba'zi JS-widgetlarda uchraydi)
    for attr_name in ("data-timestamp", "data-created-at", "data-posted-at", "data-datetime"):
        el = article.select_one(f"[{attr_name}]")
        if el and el.get(attr_name):
            raw = el.get(attr_name).strip()
            try:
                if raw.isdigit():
                    return datetime.fromtimestamp(int(raw), tz=timezone.utc)
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except (ValueError, OSError):
                pass

    # 3) Nisbiy vaqt matni: "5 daqiqa oldin", "3 soat oldin", "2 kun oldin",
    #    "bugun", "kecha" va shunga o'xshash iboralar.
    text = _clean_text(article.get_text(" ", strip=True)).lower()

    m = re.search(
        r"\b(\d{1,3})\s*(daqiqa|minut|min|soat|soatlar|soatdan|kun|kunlar|hafta)\b",
        text,
    )
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith(("daqiqa", "minut", "min")):
            delta = timedelta(minutes=amount)
        elif unit.startswith("soat"):
            delta = timedelta(hours=amount)
        elif unit.startswith("hafta"):
            delta = timedelta(weeks=amount)
        else:  # kun / kunlar
            delta = timedelta(days=amount)
        return datetime.now(timezone.utc) - delta

    if re.search(r"\bhozir(gina)?\b|\bjust now\b", text):
        return datetime.now(timezone.utc)
    if re.search(r"\bbugun\b|\btoday\b", text):
        return datetime.now(timezone.utc)
    if re.search(r"\bkecha\b|\byesterday\b", text):
        return datetime.now(timezone.utc) - timedelta(days=1)

    return None


def fetch_posts(session: requests.Session, filters: dict) -> list:
    """
    dyor.net/posts sahifasidan (login qilingan sessiya orqali) har bir
    RODY AI signal postini to'liq ma'lumotlar bilan chiqarib oladi.

    `filters` — timeframe ro'yxatini dinamik to'ldirish uchun beriladi:
    yangi timeframe qiymati uchrasa, standart holatda "yoqilgan" deb
    filters["timeframes"]'ga qo'shiladi (shu bilan /filters panelida darhol
    ko'rinadigan bo'ladi).
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
        # Avval bu holat jimgina "muvaffaqiyat" bo'lib o'tib ketardi: bot
        # "Jami topilgan post: 0" deb tugardi va hech kim xabardor bo'lmasdi.
        # Endi xato sifatida chiqadi -> workflow qizil bo'ladi va
        # ADMIN_CHAT_ID'ga ogohlantirish boradi.
        raise RuntimeError(
            f"/posts sahifasida birorta ham post topilmadi (final_url={resp.url}). "
            "Sabablari: login sessiyasi ishlamadi, Premium/trial muddati tugadi "
            "(sahifa /pricing yoki /login'ga yo'naltirilgan bo'lishi mumkin), "
            "yoki sayt HTML tuzilishi o'zgardi."
        )
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
        if timeframe:
            tf_map = filters.setdefault("timeframes", {})
            if timeframe not in tf_map:
                tf_map[timeframe] = True  # yangi timeframe -> standart holatda yoqilgan

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

        posted_at = _extract_post_datetime(article)
        if posted_at is None:
            print(
                f"[debug] Post sanasi topilmadi (post_id={post_id}). "
                "Sana filtri bu post uchun qo'llanilmaydi (xavfsiz tomonda xato qilinadi).",
                file=sys.stderr,
            )

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
                "posted_at": posted_at,
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


# ---------------------------------------------------------------------------
# FILTERLASH MANTIG'I
# ---------------------------------------------------------------------------


def passes_filters(post: dict, filters: dict) -> bool:
    """Postning yo'nalish/timeframe/ishonch/coin filterlaridan o'tishini tekshiradi."""
    direction = (post.get("direction") or "").strip().lower()
    if direction in ("bullish", "bearish"):
        if not filters.get("directions", {}).get(direction, True):
            return False

    timeframe = post.get("timeframe")
    if timeframe:
        if not filters.get("timeframes", {}).get(timeframe, True):
            return False

    confidence = post.get("confidence")
    if confidence:
        m = re.search(r"(\d+(?:\.\d+)?)", confidence)
        if m:
            value = float(m.group(1))
            # Sayt ishonchni "8.1/10" ko'rinishida beradi, filter esa foizda
            # (0/50/60/70/80/90). Avval 8.1 to'g'ridan-to'g'ri 50 bilan
            # solishtirilardi -> "Min ishonch" tugmasi bir marta bosilsa,
            # BARCHA postlar filtrlanib, kanalga hech narsa yuborilmay qolardi.
            if "/10" in confidence.replace(" ", "") or value <= 10:
                value *= 10  # 8.1/10 -> 81%
            if value < filters.get("min_confidence", 0):
                return False

    if filters.get("symbol_mode") == "whitelist":
        symbol = (post.get("symbol") or "").strip().upper()
        whitelist = [s.upper() for s in filters.get("symbol_whitelist", [])]
        if symbol not in whitelist:
            return False

    return True


def classify_post(post: dict, filters: dict, now: datetime) -> str:
    """
    Har bir post uchun qaror qaytaradi:
      "send"        -> filterlardan o'tdi, kanalga yuboriladi
      "skip_age"    -> juda eski (doimiy o'tkazib yuboriladi, qayta tekshirilmaydi)
      "skip_filter" -> hozirgi filterga mos kelmadi (filter o'zgarsa, keyingi
                        runlarda qayta baholanadi, chunki bu holatda seen_ids'ga
                        qo'shilmaydi)
    """
    if filters.get("only_last_24h", True):
        posted_at = post.get("posted_at")
        if posted_at is not None:
            max_age = filters.get("max_age_hours", 24)
            age_hours = (now - posted_at).total_seconds() / 3600
            if age_hours > max_age:
                return "skip_age"

    if not passes_filters(post, filters):
        return "skip_filter"

    return "send"


# ---------------------------------------------------------------------------
# TELEGRAM API YORDAMCHILARI
# ---------------------------------------------------------------------------


def tg_request(method: str, payload: dict, timeout: int = 20) -> Optional[dict]:
    if not BOT_TOKEN:
        print("XATOLIK: BOT_TOKEN o'rnatilmagan.", file=sys.stderr)
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        print(f"Telegram API so'rovida xatolik ({method}): {exc}", file=sys.stderr)
        return None

    if resp.status_code != 200:
        print(f"Telegram API xatolik ({method}): {resp.status_code} {resp.text}", file=sys.stderr)
        return None

    try:
        return resp.json()
    except ValueError:
        return None


def send_to_telegram(text: str) -> bool:
    if not CHANNEL_ID:
        print("XATOLIK: CHANNEL_ID o'rnatilmagan.", file=sys.stderr)
        return False

    result = tg_request(
        "sendMessage",
        {
            "chat_id": CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
    )
    return bool(result and result.get("ok"))


def send_message(chat_id, text: str, keyboard: Optional[list] = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    tg_request("sendMessage", payload)


def edit_message_keyboard(chat_id, message_id, keyboard: list) -> None:
    tg_request(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": keyboard},
        },
    )


def answer_callback_query(callback_id: str, text: Optional[str] = None) -> None:
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    tg_request("answerCallbackQuery", payload)


def get_updates(offset: int) -> list:
    result = tg_request(
        "getUpdates",
        {"offset": offset, "timeout": 0, "allowed_updates": ["message", "callback_query"]},
    )
    if result and result.get("ok"):
        return result.get("result", [])
    return []


# ---------------------------------------------------------------------------
# ADMIN FILTER PANELI (/filters)
# ---------------------------------------------------------------------------


def build_keyboard(filters: dict) -> list:
    rows = []

    directions = filters.get("directions", {})
    rows.append(
        [
            {
                "text": f"{'✅' if directions.get('bullish', True) else '❌'} Bullish",
                "callback_data": "dir:bullish",
            },
            {
                "text": f"{'✅' if directions.get('bearish', True) else '❌'} Bearish",
                "callback_data": "dir:bearish",
            },
        ]
    )

    timeframes = filters.get("timeframes", {})
    if timeframes:
        tf_row = []
        for tf in sorted(timeframes.keys()):
            tf_row.append(
                {"text": f"{'✅' if timeframes[tf] else '❌'} {tf}", "callback_data": f"tf:{tf}"}
            )
            if len(tf_row) == 3:
                rows.append(tf_row)
                tf_row = []
        if tf_row:
            rows.append(tf_row)
    else:
        rows.append(
            [{"text": "⏱ Timeframe ro'yxati hali bo'sh (birinchi fetchdan keyin to'ladi)", "callback_data": "noop"}]
        )

    rows.append(
        [
            {
                "text": f"🎯 Min ishonch: {filters.get('min_confidence', 0)}% (o'zgartirish uchun bosing)",
                "callback_data": "conf:cycle",
            }
        ]
    )

    mode = filters.get("symbol_mode", "all")
    whitelist = filters.get("symbol_whitelist", [])
    if mode == "all":
        sym_text = "🌐 Coinlar: Barchasi (whitelist rejimiga o'tish uchun bosing)"
    else:
        sym_text = f"📋 Coinlar: Faqat ro'yxatdagilar ({len(whitelist)} ta) — barchasi uchun bosing"
    rows.append([{"text": sym_text, "callback_data": "sym:mode"}])

    age_on = filters.get("only_last_24h", True)
    rows.append(
        [
            {
                "text": (
                    f"🕐 Faqat oxirgi {filters.get('max_age_hours', 24)} soat: "
                    f"{'✅ Yoqilgan' if age_on else '❌ Ochirilgan'}"
                ),
                "callback_data": "age:toggle",
            }
        ]
    )

    return rows


def filters_summary_text(filters: dict) -> str:
    whitelist = filters.get("symbol_whitelist", [])
    whitelist_text = ", ".join(whitelist) if whitelist else "(bo'sh)"
    return (
        "🎛 <b>DYOR bot — filter sozlamalari</b>\n\n"
        "Quyidagi tugmalar orqali qaysi signallar kanalga yuborilishini boshqaring.\n"
        "✅ — yoqilgan (yuboriladi), ❌ — o'chirilgan (yuborilmaydi).\n\n"
        f"📋 Coin whitelist: {whitelist_text}\n"
        "Qo'shish: <code>/addcoin BTC</code> | Olib tashlash: <code>/removecoin BTC</code> | "
        "Ko'rish: <code>/coins</code>\n\n"
        "⚠️ Bot GitHub Actions orqali davriy ishga tushadi (doimiy tinglamaydi), "
        "shuning uchun tugma bosilgandan keyin o'zgarish ~10 daqiqa ichida kuchga kiradi."
    )


def handle_admin_command(text: str, chat_id, filters: dict) -> None:
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/start", "/filters"):
        send_message(chat_id, filters_summary_text(filters), keyboard=build_keyboard(filters))
    elif cmd == "/addcoin" and arg:
        symbol = arg.upper()
        whitelist = filters.setdefault("symbol_whitelist", [])
        if symbol not in whitelist:
            whitelist.append(symbol)
        send_message(
            chat_id,
            f"✅ {symbol} whitelist ro'yxatiga qo'shildi.\nJoriy ro'yxat: {', '.join(whitelist)}",
        )
    elif cmd == "/removecoin" and arg:
        symbol = arg.upper()
        whitelist = filters.setdefault("symbol_whitelist", [])
        if symbol in whitelist:
            whitelist.remove(symbol)
            send_message(chat_id, f"🗑 {symbol} whitelist ro'yxatidan olib tashlandi.")
        else:
            send_message(chat_id, f"{symbol} ro'yxatda topilmadi.")
    elif cmd == "/coins":
        whitelist = filters.get("symbol_whitelist", [])
        listing = ", ".join(whitelist) if whitelist else "(bo'sh)"
        send_message(chat_id, f"📋 Joriy whitelist: {listing}")
    elif cmd == "/help":
        send_message(
            chat_id,
            "Buyruqlar:\n"
            "/filters — filter panelini ko'rsatish\n"
            "/addcoin SYMBOL — whitelist'ga coin qo'shish\n"
            "/removecoin SYMBOL — whitelist'dan coin olib tashlash\n"
            "/coins — joriy whitelist'ni ko'rish",
        )
    else:
        send_message(chat_id, "Noma'lum buyruq. Buyruqlar ro'yxati uchun /help yuboring.")


def handle_callback(callback: dict, filters: dict) -> None:
    data = callback.get("data", "")
    message = callback.get("message", {}) or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")

    if data == "noop":
        answer_callback_query(callback["id"])
        return

    if data.startswith("dir:"):
        key = data.split(":", 1)[1]
        directions = filters.setdefault("directions", {})
        directions[key] = not directions.get(key, True)
    elif data.startswith("tf:"):
        key = data.split(":", 1)[1]
        timeframes = filters.setdefault("timeframes", {})
        timeframes[key] = not timeframes.get(key, True)
    elif data == "conf:cycle":
        options = filters.get("confidence_options", [0, 50, 60, 70, 80, 90])
        current = filters.get("min_confidence", 0)
        idx = options.index(current) if current in options else -1
        filters["min_confidence"] = options[(idx + 1) % len(options)]
    elif data == "sym:mode":
        filters["symbol_mode"] = "whitelist" if filters.get("symbol_mode", "all") == "all" else "all"
    elif data == "age:toggle":
        filters["only_last_24h"] = not filters.get("only_last_24h", True)

    answer_callback_query(callback["id"], text="Yangilandi ✅")
    if chat_id is not None and message_id is not None:
        edit_message_keyboard(chat_id, message_id, build_keyboard(filters))


def process_admin_updates(filters: dict) -> None:
    """
    Admin (ADMIN_CHAT_ID) tomonidan yuborilgan buyruq/tugma bosishlarini
    tekshiradi va `filters`ni shunga qarab o'zgartiradi (joyida mutatsiya
    qiladi). ADMIN_CHAT_ID yoki BOT_TOKEN yo'q bo'lsa, hech narsa qilmaydi.
    """
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        return

    offset = filters.get("last_update_id", 0)
    updates = get_updates(offset)

    for update in updates:
        filters["last_update_id"] = update["update_id"] + 1

        message = update.get("message")
        callback = update.get("callback_query")

        if message:
            chat_id = message.get("chat", {}).get("id")
            if str(chat_id) != str(ADMIN_CHAT_ID):
                continue  # faqat admin buyruqlarni qabul qilamiz
            text = (message.get("text") or "").strip()
            if text:
                handle_admin_command(text, chat_id, filters)
        elif callback:
            chat_id = (callback.get("message") or {}).get("chat", {}).get("id")
            if str(chat_id) != str(ADMIN_CHAT_ID):
                answer_callback_query(callback["id"])
                continue
            handle_callback(callback, filters)


# ---------------------------------------------------------------------------
# ASOSIY OQIM
# ---------------------------------------------------------------------------


def main() -> None:
    filters = load_filters()
    seen = load_seen_ids()

    login_error: Optional[Exception] = None
    posts: list = []
    session: Optional[requests.Session] = None

    try:
        session = get_authenticated_session()
    except (requests.RequestException, RuntimeError) as exc:
        login_error = exc

    if session is not None:
        try:
            posts = fetch_posts(session, filters)
        except (requests.RequestException, RuntimeError) as exc:
            login_error = exc

    # Admin filter buyruqlari/tugmalari — login yoki fetch xato bo'lsa ham
    # tekshiriladi, shunda dyor.net vaqtincha ishlamay qolsa ham siz
    # filterlarni boshqarishda davom eta olasiz.
    try:
        process_admin_updates(filters)
    except requests.RequestException as exc:
        print(f"[warn] Telegram admin buyruqlarini olishda xatolik: {exc}", file=sys.stderr)

    # Filterlarni darhol saqlaymiz: admin tugmalari va yangi topilgan
    # timeframe'lar keyingi xato bo'lsa ham yo'qolib qolmasin.
    save_filters(filters)

    if login_error is not None:
        print(f"Xatolik: {login_error}", file=sys.stderr)
        save_seen_ids(seen)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    new_count = 0
    skipped_filter = 0
    skipped_age = 0
    send_failed = 0

    # --- DIAGNOSTIKA: saytda nimalar ko'rindi va ulardan qaysilari yangi ---
    page_ids = [make_post_id(p) for p in posts]
    unseen_ids = [pid for pid in page_ids if pid not in seen]
    print(f"[debug] sahifadagi postlar: {page_ids}", file=sys.stderr)
    print(f"[debug] ulardan hali yuborilmaganlari: {unseen_ids or 'yoq'}", file=sys.stderr)
    if not unseen_ids:
        print(
            "[debug] Saytda yangi post yo'q — bot to'g'ri ishlayapti, "
            "shunchaki yangi signal chiqishini kutyapti.",
            file=sys.stderr,
        )

    for post in reversed(posts):
        pid = make_post_id(post)
        if pid in seen:
            continue

        decision = classify_post(post, filters, now)
        print(
            f"[debug] {pid} ({post.get('symbol')} {post.get('timeframe')} "
            f"{post.get('direction')} {post.get('confidence')}) -> {decision}",
            file=sys.stderr,
        )

        if decision == "skip_age":
            # Doim eski qoladi -> qayta tekshirmaslik uchun seen'ga qo'shamiz.
            seen.add(pid)
            skipped_age += 1
            continue

        if decision == "skip_filter":
            # seen'ga QO'SHILMAYDI: filter keyinroq o'zgarsa, bu post
            # (agar hali "oxirgi 24 soat" ichida bo'lsa) qayta baholanadi.
            skipped_filter += 1
            continue

        message = format_message(post)
        sent = send_to_telegram(message)
        if sent:
            seen.add(pid)
            new_count += 1
            time.sleep(1.5)  # Telegram rate-limit uchun kichik pauza
        else:
            send_failed += 1

    save_seen_ids(seen)
    save_filters(filters)
    print(
        f"Tugadi. Jami topilgan post: {len(posts)}, yangi yuborilgan: {new_count}, "
        f"filterga mos kelmagani uchun o'tkazilgan: {skipped_filter}, "
        f"eski (24 soatdan katta) bo'lgani uchun o'tkazilgan: {skipped_age}, "
        f"Telegram'ga yuborib bo'lmagan: {send_failed}"
    )

    # Telegram yubora olmagan bo'lsa (masalan bot kanaldan chiqarilgan,
    # CHANNEL_ID noto'g'ri), run'ni xato qilib belgilaymiz — admin xabar oladi.
    if send_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
