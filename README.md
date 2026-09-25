# dyor.net → Telegram bot (bepul, GitHub Actions orqali)

Bu bot https://dyor.net/posts sahifasidagi yangi postlarni belgilangan Telegram kanaliga avtomatik yuboradi. Hosting uchun pul to'lanmaydi — GitHub Actions'ning bepul limitidan foydalaniladi (public repo uchun cheksiz, private repo uchun oyiga 2000 daqiqa bepul, bu bot uchun yetarlidan ortiq).

## ⚠️ Avval shuni bilib qo'ying

`bot.py` ichidagi `fetch_posts()` funksiyasidagi selectorlar **vaqtinchalik namuna**. Ular sayt tuzilishiga mos kelmasligi mumkin. To'g'irlash uchun:

1. https://dyor.net/posts sahifasini brauzerda oching.
2. Bitta post ustida o'ng tugma → **"Inspect" / "Ko'rish kodi"** bosing.
3. Post qaysi `<div>` (yoki boshqa teg)ning qaysi `class`i ichida ekanini, matn qaysi elementda, link qaysi elementda ekanini ko'ring.
4. Shu klass nomlarini `bot.py` faylidagi `TODO` deb belgilangan joylarga yozing (yoki menga shu HTML qismini yuboring — men to'g'irlab beraman).

## 1-qadam: Telegram bot yaratish

1. Telegram'da **@BotFather** ga yozing.
2. `/newbot` buyrug'ini yuboring, nom va username bering.
3. Sizga beriladigan **token**ni saqlab qo'ying (masalan: `123456:ABC-def...`).

## 2-qadam: Botni kanalga admin qilish

1. Telegram kanalingizni oching → **Administrators** → botingizni admin qilib qo'shing (kamida "Post messages" huquqi bilan).
2. Kanal ID'sini aniqlang:
   - Agar kanal ochiq (public) bo'lsa: `@kanal_username` shaklida ishlatasiz.
   - Agar yopiq (private) bo'lsa: kanalga bitta xabar yozib, so'ng
     `https://api.telegram.org/bot<TOKEN>/getUpdates` manzilini brauzerda oching va
     `"chat":{"id": -100...}` qiymatini toping — shu son sizning `CHANNEL_ID`ingiz.

## 3-qadam: Kodni GitHub'ga joylash

1. GitHub'da yangi **repository** yarating (public bo'lishi mumkin — bepul va cheksiz Actions daqiqasi beradi).
2. Shu papkadagi barcha fayllarni (`bot.py`, `requirements.txt`, `.github/`, `seen_ids.json`, `filters.json`, `README.md`) repoga yuklang.

## 4-qadam: Maxfiy kalitlarni (Secrets) qo'shish

Repo ichida: **Settings → Secrets and variables → Actions → New repository secret**

| Nomi | Qiymati |
|---|---|
| `BOT_TOKEN` | BotFather'dan olgan token |
| `CHANNEL_ID` | Kanal username yoki ID (masalan `@mening_kanalim` yoki `-1001234567890`) |
| `ADMIN_CHAT_ID` | **Filter tugmalarini boshqarish va xato xabarlarini olish uchun kerak.** Sizning shaxsiy Telegram chat ID'ingiz — pastdagi "Filter tugmalarini sozlash" bo'limiga qarang. Bo'lmasa ham bot ishlayveradi, lekin siz filterlarni tugmalar orqali boshqara olmaysiz (standart holatda hammasi yoqilgan bo'ladi). |

## 5-qadam: Ishga tushirish

- Workflow avtomatik ravishda **har 10 daqiqada** ishlaydi (`.github/workflows/bot.yml` ichida `cron` qatorini o'zgartirib chastotani sozlashingiz mumkin).
- Qo'lda sinab ko'rish uchun: repo'da **Actions** bo'limi → "DYOR -> Telegram forwarder" → **Run workflow**.

## Qanday ishlaydi (dublikatlarsiz)

- Bot har ishga tushganda `seen_ids.json` faylidan avval yuborilgan postlar ro'yxatini o'qiydi.
- Yangi postlarni yuboradi, so'ng ro'yxatni yangilab, workflow avtomatik ravishda uni qaytadan repoga commit qiladi.
- Shu tufayli bir xil post ikki marta yuborilmaydi, va bepul serverda "holat" (state) saqlanadi — alohida bazaga ehtiyoj yo'q.

## 24/7 barqaror ishlashi uchun qo'shilgan qadamlar

GitHub Actions'ning ikkita "yashirin" cheklovi bor, va workflow endi ularning ikkalasiga ham qarshi choralarga ega:

### 1. 60 kunlik "faolsizlik" bo'yicha auto-disable

GitHub qoidasi: agar repoda **60 kun davomida hech qanday commit bo'lmasa**, scheduled (`cron`) workflow **avtomatik o'chirib qo'yiladi**. Bu bot esa `seen_ids.json`ni faqat **yangi post topilganda** commit qiladi — demak, agar dyor.net saytida uzoq vaqt (60+ kun) yangi post chiqmasa, workflow jimgina o'chib qolishi mumkin edi.

Buning oldini olish uchun har run oxirida **"Workflow'ni faol saqlash"** qadami ishlaydi. U GitHub API'ning "enable workflow" chaqiruvini runner'da oldindan o'rnatilgan `gh` CLI orqali bajaradi: tashqi action ishlatilmaydi, dummy commit ham qilinmaydi. Buning uchun workflow'da `actions: write` ruxsati berilgan.

> Avval bu yerda `gautamkrishnar/keepalive-workflow` action'i ishlatilgan edi. U GitHub tomonidan bloklandi va barcha run'lar "Repository access blocked" xatosi bilan boshlanmasdanoq to'xtab qolardi.

### 2. Xato haqida xabar (login muvaffaqiyatsiz, sayt strukturasi o'zgargan va h.k.)

Avval bot xato bersa, buni faqat GitHub'ning **Actions** bo'limiga kirib logdan ko'rish mumkin edi. Endi run muvaffaqiyatsiz tugasa (masalan, login ishlamay qolsa yoki `fetch_posts()` hech narsa topa olmasa), **alohida Telegram xabari** avtomatik yuboriladi — bu xabar sizning kanalingizga emas, balki `ADMIN_CHAT_ID` orqali ko'rsatgan shaxsiy chatingizga/xizmat kanalizga tushadi.

**Sozlash:** `ADMIN_CHAT_ID`ni qanday olish "Filter tugmalarini sozlash" bo'limida tushuntirilgan (bu bir xil ID ikkala maqsad — filter boshqaruvi va xato xabarlari — uchun ishlatiladi).

Agar `ADMIN_CHAT_ID` qo'shilmasa — hech narsa buzilmaydi, bu qadam shunchaki o'tkazib yuboriladi.

## Filter tugmalarini sozlash (Telegram orqali)

Kanalga qaysi signallar yuborilishini endi Telegram botning o'zi orqali, tugmalar bosib boshqarasiz — kodga tegmasdan.

### ADMIN_CHAT_ID'ni olish (shart, aks holda tugmalar ishlamaydi)

1. BotFather'da yaratgan botingizga (yuqoridagi 1-qadam) shaxsiy xabar yozing, masalan `/start`.
2. Brauzerda `https://api.telegram.org/bot<TOKEN>/getUpdates` manzilini oching (`<TOKEN>` o'rniga o'z tokeningizni qo'ying).
3. Javobdagi `"chat":{"id": 123456789, ...}` qismidan raqamni toping — shu sizning shaxsiy `chat.id`ingiz.
4. Shu raqamni `ADMIN_CHAT_ID` nomi bilan repo Secrets'ga qo'shing (yuqoridagi 4-qadamga qarang).

### Filter panelini ochish

Botga shaxsiy xabar sifatida `/filters` (yoki `/start`) yuboring. Bot sizga tugmali panel yuboradi:

| Tugma | Nima qiladi |
|---|---|
| ✅/❌ Bullish, ✅/❌ Bearish | Faqat shu yo'nalishdagi signallarni yuborish/yubormaslik |
| ✅/❌ (timeframe, masalan 1H, 4H, 1D) | Har bir timeframe alohida yoqilishi/o'chirilishi mumkin. **Diqqat:** bu ro'yxat dinamik — faqat botning avval ko'rgan timeframe qiymatlari shu yerda chiqadi, shuning uchun birinchi marta ishga tushgandan keyingina to'ladi |
| 🎯 Min ishonch: N% | Bosgan sayin 0% → 50% → 60% → 70% → 80% → 90% → yana 0% tartibida aylanadi. Faqat shu foizdan yuqori (yoki teng) `confidence`ga ega postlar yuboriladi |
| 🌐 / 📋 Coinlar | "Barchasi" va "Faqat whitelist'dagilar" rejimlari orasida almashtiradi |
| 🕐 Faqat oxirgi 24 soat | Pastda alohida tushuntirilgan — sana bo'yicha filtrni yoqib/o'chiradi |

Coin whitelist matn buyruqlar orqali boshqariladi (chunki coinlar soni cheksiz, tugma sifatida sig'maydi):
- `/addcoin BTC` — ro'yxatga qo'shadi
- `/removecoin BTC` — ro'yxatdan olib tashlaydi
- `/coins` — joriy ro'yxatni ko'rsatadi
- `/help` — buyruqlar ro'yxati

**Muhim:** bot GitHub Actions orqali davriy (masalan har 10 daqiqada) ishga tushadi, doimiy tinglab turmaydi. Shuning uchun tugma bosgandan yoki buyruq yuborgandan keyin o'zgarish darhol emas, keyingi run'da (~10 daqiqa ichida) kuchga kiradi.

Barcha sozlamalar `filters.json` faylida saqlanadi va har run oxirida avtomatik repoga commit qilinadi (xuddi `seen_ids.json` kabi).

### "Faqat oxirgi kungi signallar" (eski postlar yuborilmaydi)

Standart holatda bot faqat **oxirgi 24 soat ichida yaratilgan** postlarni kanalga yuboradi — bundan eski postlar (masalan, birinchi marta ishga tushirilganda saytda turgan eski signallar) hech qachon yuborilmaydi. Buni `/filters` panelidagi "🕐 Faqat oxirgi 24 soat" tugmasi orqali yoqib/o'chirish mumkin.

⚠️ **Muhim ogohlantirish:** post qachon yaratilganini sayt HTML kodidan aniqlash uchun yozilgan funksiya (`_extract_post_datetime` — `bot.py` ichida) men saytning haqiqiy sana/vaqt belgisini ko'rmasdan, umumiy taxminlar (`<time datetime="...">` yoki "3 soat oldin" kabi matnlar) asosida yozilgan. Bu **sizning saytingizda ishlamasligi mumkin**. Tekshirish uchun:

1. GitHub'da **Actions** → oxirgi run → log'larni oching.
2. `[debug] Post sanasi topilmadi...` degan qatorlar bor-yo'qligini tekshiring.
3. Agar bor bo'lsa (yoki sana filtri kutilganidek ishlamayotgan bo'lsa), https://dyor.net/posts sahifasida bitta postning sana/vaqt ko'rsatiladigan qismini (masalan "3 soat oldin" yozuvi) Inspect qilib, shu HTML qismini menga yuboring — men `_extract_post_datetime` funksiyasini aniq moslab beraman.

Sana aniqlanmagan postlar, xavfsizlik uchun, **yuborilaveradi** (ya'ni funksiya ishlamasa ham signal yo'qolib qolmaydi, faqat "faqat oxirgi kun" filtri o'sha post uchun qo'llanilmaydi).

## Mahalliy kompyuterda sinash

```bash
pip install -r requirements.txt
export BOT_TOKEN="123456:ABC..."
export CHANNEL_ID="@mening_kanalim"
export ADMIN_CHAT_ID="123456789"   # ixtiyoriy, filter tugmalari uchun
python bot.py
```

## Eslatma

- Saytni tez-tez (masalan, har daqiqada) so'rash o'rniga 5-15 daqiqalik oraliq tavsiya etiladi — bu ham saytga hurmat, ham GitHub Actions limitiga tejamkor.
- dyor.net'ning foydalanish shartlarini (Terms of Service) tekshirib chiqish tavsiya etiladi, chunki avtomatik scraping ba'zi saytlarda cheklangan bo'lishi mumkin.


## Xabar formati, qisqa mazmun va Live chart

Kanalga yuboriladigan xabar ixcham ko'rinishda bo'ladi:

```
🟢 PHA/USDT · LONG · 1d · 🎯 8.1/10
Entry: 0.054719 – 0.0571
TP: 0.059955 (+7.2%)
SL: 0.0494 (−11.6%)

🇬🇧 Strong uptrend, but RSI 84 is overbought — watch for a pullback.
🇺🇿 Kuchli o'sish trendi, lekin RSI 84 — qaytish ehtimoliga e'tibor bering.

NFA
[📈 Live chart 1d] [🔎 DYOR]
```

TP va SL foizlari Entry oralig'ining o'rtasiga nisbatan hisoblanadi. Havola kartochkasi (preview) o'chirilgan.

### Qisqa mazmun (ixtiyoriy)

1. https://console.anthropic.com saytida API kalit oling (hisobda kredit bo'lishi kerak).
2. Uni `ANTHROPIC_API_KEY` nomi bilan repo Secrets'ga qo'shing.

Kalit bo'lmasa yoki API xato bersa, xabar mazmun qismisiz yuboriladi va bot ishlashda davom etadi. Model standart holatda `claude-haiku-4-5-20251001`. Uni `ANTHROPIC_MODEL` secret'i orqali o'zgartirish mumkin.

### Live chart Mini App (ixtiyoriy, lekin tavsiya etiladi)

Sozlanmagan bo'lsa, "Live chart" tugmasi TradingView saytini Telegram ichki brauzerida ochadi. Sozlansa, Telegram ichida to'liq ekranli interaktiv grafik ochiladi: kattalashtirish, surish, indikatorlar va chizish asboblari bilan, tepada Entry/TP/SL darajalari ko'rinib turadi.

1. **GitHub Pages'ni yoqing:** repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`, papka: `/docs` → Save. Bir necha daqiqadan keyin sahifa `https://<github-username>.github.io/<repo>/chart.html` manzilida ochiladi. (Bepul tarifda Pages faqat public repoda ishlaydi.)
2. **Mini App yarating:** @BotFather → `/newapp` → botingizni tanlang → nom va tavsif kiriting → rasm sifatida `docs/botfather-cover-640x360.png` ni yuboring → GIF so'rasa `/empty` → URL sifatida 1-qadamdagi manzilni kiriting → short name kiriting (masalan `chart`).
3. BotFather sizga `https://t.me/<bot_username>/chart` ko'rinishidagi havola beradi. Uni `CHART_APP_LINK` nomi bilan repo Secrets'ga qo'shing.
