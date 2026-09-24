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
2. Shu papkadagi barcha fayllarni (`bot.py`, `requirements.txt`, `.github/`, `seen_ids.json`, `README.md`) repoga yuklang.

## 4-qadam: Maxfiy kalitlarni (Secrets) qo'shish

Repo ichida: **Settings → Secrets and variables → Actions → New repository secret**

| Nomi | Qiymati |
|---|---|
| `BOT_TOKEN` | BotFather'dan olgan token |
| `CHANNEL_ID` | Kanal username yoki ID (masalan `@mening_kanalim` yoki `-1001234567890`) |
| `ADMIN_CHAT_ID` | *(ixtiyoriy, lekin tavsiya etiladi)* Bot ishlamay qolsa xato xabari keladigan shaxsiy chat/kanal ID'si — pastdagi "Xato haqida xabar" bo'limiga qarang |

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

Buning oldini olish uchun har bir run boshida **"Repo faolligini saqlab turish"** qadami qo'shildi (`gautamkrishnar/keepalive-workflow`). Bu qadam:
- Oxirgi commitdan necha kun o'tganini tekshiradi;
- Agar 45+ kun bo'lsa, alohida `keepalive` branch'iga kichik "heartbeat" commit qo'shadi (asosiy `main` branch va `seen_ids.json`ga tegmaydi);
- Aks holda hech narsa qilmaydi — resurs sarflamaydi.

### 2. Xato haqida xabar (login muvaffaqiyatsiz, sayt strukturasi o'zgargan va h.k.)

Avval bot xato bersa, buni faqat GitHub'ning **Actions** bo'limiga kirib logdan ko'rish mumkin edi. Endi run muvaffaqiyatsiz tugasa (masalan, login ishlamay qolsa yoki `fetch_posts()` hech narsa topa olmasa), **alohida Telegram xabari** avtomatik yuboriladi — bu xabar sizning kanalingizga emas, balki `ADMIN_CHAT_ID` orqali ko'rsatgan shaxsiy chatingizga/xizmat kanalizga tushadi.

**Sozlash (ixtiyoriy, lekin tavsiya etiladi):**
1. Botga (yoki istalgan botga) shaxsiy xabar yozing (`/start`).
2. `https://api.telegram.org/bot<TOKEN>/getUpdates` orqali o'zingizning shaxsiy `chat.id`ingizni toping (xuddi kanal ID topgandagidek).
3. Shu ID'ni `ADMIN_CHAT_ID` nomi bilan repo Secrets'ga qo'shing.

Agar `ADMIN_CHAT_ID` qo'shilmasa — hech narsa buzilmaydi, bu qadam shunchaki o'tkazib yuboriladi.

## Mahalliy kompyuterda sinash

```bash
pip install -r requirements.txt
export BOT_TOKEN="123456:ABC..."
export CHANNEL_ID="@mening_kanalim"
python bot.py
```

## Eslatma

- Saytni tez-tez (masalan, har daqiqada) so'rash o'rniga 5-15 daqiqalik oraliq tavsiya etiladi — bu ham saytga hurmat, ham GitHub Actions limitiga tejamkor.
- dyor.net'ning foydalanish shartlarini (Terms of Service) tekshirib chiqish tavsiya etiladi, chunki avtomatik scraping ba'zi saytlarda cheklangan bo'lishi mumkin.
