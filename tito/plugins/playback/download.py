# ==============================================================================
# download.py - Audio Download Command
# ==============================================================================
# Handles /تحميل, /تنزيل, /نزل, /download, /dl commands.
# Downloads the requested track and sends the actual audio FILE in the chat,
# without joining the voice chat or playing it in a call.
# ==============================================================================

import asyncio
import logging
import os

from pyrogram import filters, types
from pyrogram.errors import FloodWait, ChatSendPlainForbidden, ChatWriteForbidden

from tito import app, config, lang, yt
from tito.helpers import utils

logger = logging.getLogger(__name__)


async def safe_edit(message, text, **kwargs):
    try:
        await message.edit_text(text, **kwargs)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await message.edit_text(text, **kwargs)
            return True
        except Exception:
            return False
    except Exception:
        return False


async def safe_reply(message, text, **kwargs):
    try:
        return await message.reply_text(text, **kwargs)
    except (ChatSendPlainForbidden, ChatWriteForbidden):
        logger.warning(f"Cannot send text in chat {message.chat.id} (chat write forbidden)")
        return None
    except Exception as e:
        logger.error(f"Failed to send reply: {e}")
        return None


@app.on_message(
    filters.command(
        ["نزل", "تحميل", "تنزيل", "download", "dl"],
        prefixes=["", "/"],
    )
    & (filters.group | filters.channel)
    & ~app.bl_users
)
@lang.language()
async def download_hndlr(_, m: types.Message) -> None:
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass

    # Need either a query, a URL, or a reply
    url = yt.url(m)
    if not url and len(m.command) < 2:
        await safe_reply(
            m,
            "<blockquote><b>طريقة الاستخدام:</b> "
            "<code>تحميل [اسم الأغنية / رابط يوتيوب]</code></blockquote>",
        )
        return

    if url and not yt.valid(url):
        await safe_reply(m, m.lang["play_unsupported"])
        return

    try:
        sent = await safe_reply(m, m.lang["play_searching"].format(m.lang["play_emoji"]))
    except Exception:
        return
    if not sent:
        return

    # Search / resolve track
    query = url or " ".join(m.command[1:])
    file = await yt.search(query, sent.id)

    if not file:
        await safe_edit(sent, m.lang["play_not_found"].format(config.SUPPORT_CHAT))
        return

    if file.is_live:
        await safe_edit(
            sent,
            "<blockquote>❌ متقدرش تحمل البث المباشر كملف.</blockquote>",
        )
        return

    if file.duration_sec > config.DURATION_LIMIT:
        await safe_edit(
            sent,
            m.lang["play_duration_limit"].format(config.DURATION_LIMIT // 60),
        )
        return

    await safe_edit(sent, f"⬇️ <b>جارِ تحميل:</b> {utils.esc(file.title)}")

    # Download the actual audio file
    try:
        file_path = await yt.download(file.id, is_live=False, video=False)
    except Exception as e:
        logger.error(f"Download failed: {e}")
        file_path = None

    if not file_path or not os.path.exists(file_path):
        await safe_edit(
            sent,
            "<blockquote>❌ فشل تحميل الملف.\n\n"
            "الأسباب المحتملة:\n"
            "• يوتيوب اكتشف نشاط بوت (حدّث الكوكيز)\n"
            "• الفيديو محظور جغرافيًا أو خاص\n"
            "• محتوى مقيّد بالعمر (يحتاج كوكيز)</blockquote>"
        )
        return

    # Send the audio file itself in the chat
    try:
        await m.reply_audio(
            audio=file_path,
            title=file.title,
            performer=file.channel_name or None,
            duration=file.duration_sec or None,
            caption=f"🎵 {utils.esc(file.title)}",
        )
        await sent.delete()
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await m.reply_audio(
                audio=file_path,
                title=file.title,
                performer=file.channel_name or None,
                duration=file.duration_sec or None,
                caption=f"🎵 {utils.esc(file.title)}",
            )
            await sent.delete()
        except Exception as e:
            logger.error(f"Failed to send audio after FloodWait: {e}")
            await safe_edit(sent, "<blockquote>❌ فشل إرسال الملف.</blockquote>")
    except Exception as e:
        logger.error(f"Failed to send audio: {e}")
        await safe_edit(sent, "<blockquote>❌ فشل إرسال الملف (ممكن يكون حجمه أكبر من حد تيليجرام).</blockquote>")
