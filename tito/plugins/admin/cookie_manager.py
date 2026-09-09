# ==============================================================================
# cookie_manager.py - "🍪 كوكيز يوتيوب" Cookie Update Panel
# ==============================================================================
# Lets sudo users refresh the YouTube cookies used for downloads WITHOUT
# touching the server or the .env file, two ways:
#
#   1) 📎 رفع ملف - send a cookies.txt (Netscape format) file straight to
#      the bot. It's saved into tito/cookies/ and picked up immediately.
#
#   2) 🔗 لينك اونلاين - paste a link to hosted cookie content (same trusted
#      paste sites already accepted by COOKIE_URL in .env: batbin.me,
#      pastebin.com, paste.ee, rentry.co). The bot downloads it right away
#      AND remembers the link in the DB, so it keeps periodically
#      re-downloading from it in the background (see
#      tito/plugins/events/cookie_refresh.py) and survives a restart
#      (see yt.sync_cookie_urls(), called at boot in __main__.py).
#
# Command: /cookies or كوكيز - sudo users only (same gate as every other
# admin command in this bot, app.sudo_filter).
# ==============================================================================

import os

from pyrogram import filters, types

from tito import app, config, db, logger, yt
from tito.helpers import buttons

VALID_SOURCES = ("batbin.me", "pastebin.com", "paste.ee", "rentry.co")

# pending[user_id] = "upload" | "link" - which input we're waiting for next
pending: dict[int, str] = {}


def _status_text() -> str:
    file_count = 0
    if os.path.isdir("tito/cookies"):
        file_count = len([f for f in os.listdir("tito/cookies") if f.endswith(".txt")])

    return (
        "<u><b>🍪 كوكيز يوتيوب</b></u>\n\n"
        f"عدد ملفات الكوكيز الحالية: <b>{file_count}</b>\n"
        f"عدد اللينكات المتظبطة (أونلاين): <b>{len(config.COOKIES_URL)}</b>\n\n"
        "اختار طريقة التحديث:"
    )


@app.on_message(filters.command(["كوكيز", "cookies"], prefixes=["", "/"]) & app.sudo_filter)
async def _cookie_panel(_, m: types.Message):
    pending.pop(m.from_user.id, None)
    await m.reply_text(_status_text(), reply_markup=buttons.cookie_panel_markup())


@app.on_callback_query(filters.regex(r"^cookie_upload$") & app.sudo_filter)
async def _cookie_upload_start(_, query: types.CallbackQuery):
    await query.answer()
    pending[query.from_user.id] = "upload"
    await query.message.edit_text(
        "<u><b>📎 رفع ملف Cookies</b></u>\n\n"
        "ابعتلي ملف الـ cookies.txt (بصيغة Netscape) كـ document.\n"
        "لو مش عارف تصدّره، فيه إضافات متصفح زي \"Get cookies.txt LOCALLY\" "
        "بتعمله من حسابك على يوتيوب.",
        reply_markup=buttons.cookie_cancel_markup(),
    )


@app.on_callback_query(filters.regex(r"^cookie_link$") & app.sudo_filter)
async def _cookie_link_start(_, query: types.CallbackQuery):
    await query.answer()
    pending[query.from_user.id] = "link"
    await query.message.edit_text(
        "<u><b>🔗 لينك اونلاين</b></u>\n\n"
        "ابعتلي لينك محتوى الكوكيز، لازم يكون من واحد من المواقع دي:\n"
        f"<code>{', '.join(VALID_SOURCES)}</code>\n\n"
        "البوت هيحمّله على طول، وهيفضل يحدّثه لوحده كل فترة تلقائيًا.",
        reply_markup=buttons.cookie_cancel_markup(),
    )


@app.on_callback_query(filters.regex(r"^cookie_cancel$") & app.sudo_filter)
async def _cookie_cancel(_, query: types.CallbackQuery):
    pending.pop(query.from_user.id, None)
    await query.answer("تم الإلغاء.")
    await query.message.edit_text(_status_text(), reply_markup=buttons.cookie_panel_markup())


def _pending_filter(stage: str):
    def fn(_, __, message: types.Message) -> bool:
        return (
            bool(message.chat)
            and message.chat.type.name == "PRIVATE"
            and bool(message.from_user)
            and pending.get(message.from_user.id) == stage
        )
    return filters.create(fn)


@app.on_message(_pending_filter("upload") & app.sudo_filter)
async def _cookie_receive_upload(_, m: types.Message):
    if not m.document:
        return await m.reply_text("⚠️ ابعت الملف كـ document (مش كصورة أو نص).")

    if not (m.document.file_name or "").lower().endswith(".txt"):
        return await m.reply_text("⚠️ الملف لازم يكون .txt (Netscape cookie format).")

    os.makedirs("tito/cookies", exist_ok=True)
    safe_name = f"uploaded_{m.document.file_unique_id}.txt"
    dest = f"tito/cookies/{safe_name}"

    try:
        await m.download(file_name=dest)
    except Exception as e:
        logger.error(f"Cookie upload failed: {e}")
        return await m.reply_text(f"❌ فشل حفظ الملف: {type(e).__name__}")

    if not os.path.exists(dest) or os.path.getsize(dest) < 10:
        return await m.reply_text("❌ الملف فاضي أو مش صحيح.")

    # Force get_cookies() to rescan tito/cookies/ next time it's called,
    # so the new file is picked up immediately without a restart.
    yt.checked = False
    yt.cookies = []

    pending.pop(m.from_user.id, None)
    logger.info(f"🍪 New cookies file uploaded by {m.from_user.id}: {safe_name}")
    await m.reply_text(
        f"✅ اتحفظ الملف وهيتستخدم على طول من دلوقتي: <code>{safe_name}</code>",
        reply_markup=buttons.cookie_panel_markup(),
    )


@app.on_message(_pending_filter("link") & app.sudo_filter)
async def _cookie_receive_link(_, m: types.Message):
    if not m.text:
        return await m.reply_text("⚠️ ابعت اللينك كنص.")

    url = m.text.strip()
    if not any(source in url for source in VALID_SOURCES):
        return await m.reply_text(
            "⚠️ اللينك لازم يكون من واحد من المواقع دي بس:\n"
            f"<code>{', '.join(VALID_SOURCES)}</code>"
        )

    try:
        await yt.refresh_cookies([url] + [u for u in config.COOKIES_URL if u != url])
    except Exception as e:
        logger.error(f"Cookie link refresh failed: {e}")
        return await m.reply_text(f"❌ فشل تحميل الكوكيز من اللينك: {type(e).__name__}")

    if not yt.cookies:
        return await m.reply_text(
            "❌ اللينك اتحمل بس مفيهوش كوكيز صحيحة. راجع المحتوى وابعت تاني."
        )

    if url not in config.COOKIES_URL:
        config.COOKIES_URL.append(url)
    await db.add_cookie_url(url)

    pending.pop(m.from_user.id, None)
    logger.info(f"🍪 New cookie URL added by {m.from_user.id}: {url}")
    await m.reply_text(
        "✅ اللينك اتضاف واتحمل. البوت هيفضل يحدّثه لوحده كل فترة، "
        "وهيفضل متظبط حتى لو عملت ريستارت.",
        reply_markup=buttons.cookie_panel_markup(),
    )
