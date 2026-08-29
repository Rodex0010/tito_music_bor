# ==============================================================================
# example_radio.py - Online Radio Streams (completed)
# ==============================================================================
# /راديو (or /radio) -> shows a photo (config.RADIO_IMG) with a button grid of
# live radio stations. Tapping a button (or using /راديو <name>) force-starts
# that station's stream in the voice chat - same "jump the queue" behaviour
# as /شغل_فرض, so it interrupts whatever is currently playing.
#
# Add / remove stations by editing RADIO_STATIONS below. Each entry needs a
# direct, always-on stream URL (an .m3u8/.mp3/.aac icecast-style link -
# NOT a normal webpage). ffmpeg (used under the hood by yt/tune) can read
# these directly, so no download step is needed - that's what makes radio
# playback uninterrupted even when YouTube extraction is having issues.
# ==============================================================================

import logging

from pyrogram import enums, filters, types

from tito import app, config, db, queue, tune
from tito.helpers import Media, buttons, can_manage_vc, utils

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Station list - key -> (display name, stream url)
# Feel free to add/replace stations with whatever streams you want.
# --------------------------------------------------------------------------
RADIO_STATIONS = {
    "quran": ("📖 القرآن الكريم", "https://qurango.net/radio/tarateel"),
    "sunnah": ("🕌 إذاعة السنة النبوية", "https://n0c.radiojar.com/8s5u5tpdtwzuv"),
    "quran_kids": ("🧒 قرآن للأطفال", "https://backup.qurango.net/radio/quran_walid_alsamet"),
    "nogoom": ("🎵 نجوم إف إم", "https://stream.zeno.fm/f3wvbbqmdg8uv"),
    "hits": ("🔥 راديو هيتس", "https://n10.radiojar.com/8s5u5tpdtwzuv"),
}

RADIO_CAPTION = (
    "<blockquote>"
    "📻 <b>راديو تيتو</b>\n\n"
    "اختار محطة من الأزرار تحت عشان تشتغل في الشات الصوتي على طول.\n"
    "أو اكتب: <code>/راديو اسم_المحطة</code>"
    "</blockquote>"
)


def _radio_markup() -> types.InlineKeyboardMarkup:
    rows = []
    row = []
    for key, (name, _url) in RADIO_STATIONS.items():
        row.append(
            buttons.ikb(text=name, callback_data=f"radio_play {key}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append(
        [
            buttons.ikb(
                text="✖️ إغلاق",
                callback_data="radio_close",
                style=enums.ButtonStyle.DANGER,
            )
        ]
    )
    return buttons.ikm(rows)


async def _ensure_assistant_joined(chat_id: int) -> bool:
    """Lightweight join-check for radio playback.

    If a call is already active (or the assistant is already a member),
    this is a no-op. Otherwise it tries to join the assistant into the
    chat - same idea as the full flow in helpers/_play.py::checkUB, just
    trimmed down since radio doesn't need the URL/force/video parsing
    that command does. Returns True if it's safe to proceed to playback.
    """
    if chat_id in db.active_calls or await db.get_call(chat_id):
        return True

    client = await db.get_client(chat_id)
    if not client:
        return False

    try:
        member = await app.get_chat_member(chat_id, client.id)
        if member and member.status in (
            enums.ChatMemberStatus.BANNED,
            enums.ChatMemberStatus.RESTRICTED,
        ):
            await app.unban_chat_member(chat_id, client.id)
            member = None
    except Exception:
        member = None

    if member is not None:
        try:
            await client.resolve_peer(chat_id)
        except Exception:
            pass
        return True

    # Assistant isn't in the chat yet - try to join it.
    try:
        chat = await app.get_chat(chat_id)
        invite_link = chat.invite_link or await app.export_chat_invite_link(chat_id)
    except Exception as e:
        logger.warning(f"radio: couldn't get invite link for {chat_id}: {e}")
        return False

    try:
        await client.join_chat(invite_link)
    except Exception as e:
        # Already in / invite pending / whatever - not fatal, keep going
        # and let play_media's own error handling catch a real failure.
        logger.info(f"radio: join_chat note for {chat_id}: {e}")

    try:
        await client.resolve_peer(chat_id)
    except Exception:
        try:
            await client.get_chat(chat_id)
        except Exception as e:
            logger.warning(f"radio: assistant still can't see {chat_id}: {e}")
            return False

    return True


async def _play_station(chat_id: int, key: str) -> tuple[bool, str]:
    station = RADIO_STATIONS.get(key)
    if not station:
        return False, "<blockquote>❌ المحطة دي مش موجودة.</blockquote>"

    name, url = station

    if not await _ensure_assistant_joined(chat_id):
        return False, (
            "<blockquote>⚠️ محتاج أكون أدمن في الجروب الأول "
            "(أو مفيش أسيستنت متاح) عشان أقدر أدخل الشات الصوتي.</blockquote>"
        )

    media = Media(
        id=f"radio_{key}",
        duration="LIVE",
        duration_sec=0,
        file_path=url,
        message_id=0,
        title=name,
        url=url,
        is_live=True,
    )

    # Force this station in right now, same as /شغل_فرض - skips whatever
    # was playing/queued instead of waiting in line behind it.
    queue.force_add(chat_id, media)

    try:
        await tune.play_media(chat_id=chat_id, message=None, media=media)
    except Exception as e:
        logger.warning(f"radio: playback failed for {chat_id} ({key}): {e}")
        return False, (
            f"<blockquote>❌ فشل تشغيل المحطة.\n"
            f"جرب تاني أو بلغ الدعم: {config.SUPPORT_CHAT}</blockquote>"
        )

    return True, f"<blockquote>📻 دلوقتي بيشتغل: <b>{utils.esc(name)}</b></blockquote>"


# --------------------------------------------------------------------------
# /راديو or /radio -> station picker
# --------------------------------------------------------------------------
@app.on_message(
    filters.command(["راديو", "radio"], prefixes=["", "/"])
    & (filters.group | filters.channel)
    & ~app.bl_users
)
@can_manage_vc
async def _radio_hndlr(_, m: types.Message) -> None:
    try:
        await m.delete()
    except Exception:
        pass

    # /راديو <name> -> play directly without showing the menu
    if len(m.command) > 1:
        key = m.command[1].strip().lower()
        if key in RADIO_STATIONS:
            ok, text = await _play_station(m.chat.id, key)
            try:
                await m.reply_text(text)
            except Exception:
                pass
            return

    try:
        await m.reply_photo(
            photo=config.RADIO_IMG,
            caption=RADIO_CAPTION,
            reply_markup=_radio_markup(),
        )
    except Exception as e:
        logger.warning(f"radio: couldn't send station menu in {m.chat.id}: {e}")
        try:
            await m.reply_text(RADIO_CAPTION, reply_markup=_radio_markup())
        except Exception:
            pass


# --------------------------------------------------------------------------
# Station button tapped
# --------------------------------------------------------------------------
@app.on_callback_query(filters.regex(r"^radio_play ") & ~app.bl_users)
@can_manage_vc
async def _radio_cb(_, cq: types.CallbackQuery) -> None:
    key = cq.data.split(" ", 1)[1]
    chat_id = cq.message.chat.id

    await cq.answer("⏳ بشغل المحطة...")

    ok, text = await _play_station(chat_id, key)

    try:
        if cq.message.photo:
            await cq.message.edit_caption(text, reply_markup=_radio_markup() if ok else None)
        else:
            await cq.message.edit_text(text, reply_markup=_radio_markup() if ok else None)
    except Exception:
        try:
            await app.send_message(chat_id, text)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Close the station menu
# --------------------------------------------------------------------------
@app.on_callback_query(filters.regex(r"^radio_close$") & ~app.bl_users)
@can_manage_vc
async def _radio_close(_, cq: types.CallbackQuery) -> None:
    try:
        await cq.message.delete()
    except Exception:
        await cq.answer()