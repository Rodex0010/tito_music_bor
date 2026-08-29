# ==============================================================================
# fsub.py - Mandatory Channel Subscription (Force-Subscribe)
# ==============================================================================
# When enabled, users must be a member of config.SUPPORT_CHANNEL
# (https://t.me/l_zor_l by default) before they can use the bot AT ALL - not
# just /play, but every command listed in GATED_COMMANDS below (playback,
# settings, games, help/start, etc). Anyone not subscribed who tries any of
# these gets a "join the channel first" prompt instead, and nothing else
# runs for that message.
#
# Can be turned on/off two ways, as requested:
#   1) A button in the owner control panel (🔔 row on the main panel) -
#      see owner_panel.py's op_fsub_toggle callback.
#   2) A text command, available to sudo users:
#        تفعيل_الاشتراك / تعطيل_الاشتراك   or   fsubon / fsuboff
#
# If the channel username can't be parsed from config.SUPPORT_CHANNEL, or the
# membership check errors out for any reason (bot not in the channel yet,
# network hiccup, etc), this fails OPEN (lets the user through) so a
# misconfiguration can never lock everyone out of the bot.
# ==============================================================================

import re

import pyrogram
from pyrogram import filters, types, enums, errors

from tito import app, config, db, lang, logger

_CHANNEL_USERNAME_RE = re.compile(r"t\.me/(?:\+)?([A-Za-z0-9_]+)")

# Every command (all languages/aliases, no prefix) that a normal user could
# type to make the bot do something. Kept in one place so the mandatory-
# subscription gate below covers the whole bot, not just /play.
GATED_COMMANDS = [
    # start / help / settings
    "بدء", "ابدأ", "ابدا", "start",
    "مساعدة", "الأوامر", "الاوامر", "help",
    "وضع_التشغيل", "اعدادات", "settings",
    # playback
    "شغل", "تشغيل", "لايف", "شغل_فرض", "شغل_فيديو", "فيديو", "فيد",
    "شغل_فيديو_فرض", "play", "p", "live", "fplay", "vplay", "video",
    "vid", "vfplay",
    "تقديم", "ترجيع", "forward", "rewind", "back",
    "تخطي", "اسكيب", "سكيب", "التالي", "skip", "next",
    "تكرار", "loop",
    "قائمة", "يعمل", "queue", "q",
    "استمرار", "resume",
    "ايقاف", "pause",
    "انهاء", "وقف", "stop", "end",
    "تحميل", "download",
    # settings / utility
    "لغة", "اللغة", "lang", "language",
    "تصريح", "الغاء_تصريح", "auth", "unauth",
    "قائمة_التصريح", "authlist",
    "تحديث_الكاش", "تحديث",
    "ادمن", "الادمن", "تبليغ", "admin", "mention",
    "البوتات", "bots",
    "احصائيات", "stats",
    "حي", "بينج", "ping",
    "تفعيل_الاذان", "azanon", "تعطيل_الاذان", "azanoff",
    # games
    "نرد", "dice", "جاكبوت", "jackpot", "سهم", "dart",
    "كرة_سلة", "basketball", "كرة", "ball", "كرة_قدم", "football",
    # admin / sudo (sudoers always bypass the gate anyway, listed here just
    # so a non-sudo user poking at these gets the join prompt too)
    "مغادرة_تلقائية", "autoleave",
    "اضافة_مشرف", "حذف_مشرف", "ازالة_مشرف", "addsudo", "delsudo", "removesudo",
    "قائمة_المشرفين", "المشرفين", "sudolist", "sudoers",
    "غادر", "leave", "غادر_الكل", "leaveall",
    "تفعيل_الفيديو", "تعطيل_الفيديو", "vplayon", "vplayoff",
    "بث", "broadcast", "ايقاف_البث", "وقف_البث", "stopbroadcast", "cancelbroadcast",
    "السجلات", "logs", "المسجل", "logger", "اعادة_تشغيل",
    "حظر_مجموعة", "blacklistchat", "فك_حظر_مجموعة", "الغاء_حظر_مجموعة", "whitelistchat",
    "المجموعات_المحظورة", "قائمة_الحظر", "blacklistedchats",
    "حظر", "block", "فك_حظر", "unblock", "المحظورين", "قائمة_المحظورين", "blocked",
    "نشط", "active",
]


def _channel_username() -> str | None:
    """Extract 'l_zor_l' out of config.SUPPORT_CHANNEL
    ('https://t.me/l_zor_l')."""
    match = _CHANNEL_USERNAME_RE.search(config.SUPPORT_CHANNEL or "")
    return match.group(1) if match else None


def fsub_markup() -> types.InlineKeyboardMarkup:
    rows = [
        [
            types.InlineKeyboardButton(
                text="📢 اشترك في القناة",
                url=config.SUPPORT_CHANNEL,
                style=enums.ButtonStyle.SUCCESS,
            )
        ],
        [
            types.InlineKeyboardButton(
                text="✅ اشتركت، تحقق",
                callback_data="fsub_recheck",
            )
        ],
    ]
    return types.InlineKeyboardMarkup(rows)


async def is_subscribed(user_id: int) -> bool:
    """True if the user is a member of the mandatory channel (or the check
    can't be performed reliably, in which case we don't block anyone)."""
    username = _channel_username()
    if not username:
        return True

    try:
        member = await app.get_chat_member(f"@{username}", user_id)
        return member.status not in (
            enums.ChatMemberStatus.LEFT,
            enums.ChatMemberStatus.BANNED,
        )
    except errors.UserNotParticipant:
        return False
    except Exception as e:
        # Bot might not be admin in the channel yet, or a transient network
        # error - never let a misconfiguration break the whole bot.
        logger.warning(f"fsub: membership check failed for {user_id}: {e}")
        return True


async def _send_prompt(m: types.Message) -> None:
    try:
        await m.reply_text(
            "<blockquote>"
            "🔔 <b>لازم تشترك في القناة الأول عشان تقدر تستخدم البوت.</b>\n\n"
            "اشترك من الزرار تحت، وبعدين دوس ✅ اشتركت."
            "</blockquote>",
            reply_markup=fsub_markup(),
        )
    except Exception:
        pass


# --------------------------------------------------------------------------
# Kept for any handler that wants to gate a single command inline, e.g.
#   filters.command([...]) & fsub_filter
# --------------------------------------------------------------------------
async def _fsub_check(_, __, m: types.Message) -> bool:
    if not m.from_user:
        return True
    if m.from_user.id in app.sudoers:
        return True
    if not await db.get_fsub_enabled():
        return True
    if await is_subscribed(m.from_user.id):
        return True

    await _send_prompt(m)
    return False


fsub_filter = filters.create(_fsub_check)


# --------------------------------------------------------------------------
# Global gate: runs before every other handler (group=-100, the lowest/
# earliest group in this bot) for any command in GATED_COMMANDS. If the
# sender isn't subscribed, it sends the join prompt and raises
# StopPropagation so nothing else - no admin check, no playback, nothing -
# runs for that message. This is what makes the mandatory subscription
# cover the WHOLE bot instead of just /play.
# --------------------------------------------------------------------------
@app.on_message(
    filters.command(GATED_COMMANDS, prefixes=["", "/"]),
    group=-100,
)
async def _fsub_gate(_, m: types.Message):
    if not m.from_user:
        return
    if m.from_user.id in app.sudoers:
        return
    if not await db.get_fsub_enabled():
        return
    if await is_subscribed(m.from_user.id):
        return

    await _send_prompt(m)
    try:
        await m.delete()
    except Exception:
        pass
    raise pyrogram.StopPropagation


@app.on_callback_query(filters.regex(r"^fsub_recheck$") & ~app.bl_users)
async def _fsub_recheck(_, query: types.CallbackQuery):
    if not query.from_user:
        return await query.answer()

    if await is_subscribed(query.from_user.id):
        await query.answer("✅ تم التحقق، جرّب الأمر تاني.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
    else:
        await query.answer("❌ لسه مانضمتش للقناة، اشترك الأول.", show_alert=True)


@app.on_message(
    filters.command(
        ["تفعيل_الاشتراك", "تعطيل_الاشتراك", "fsubon", "fsuboff"],
        prefixes=["", "/"],
    )
    & app.sudo_filter
)
@lang.language()
async def _toggle_fsub(_, m: types.Message):
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass

    enable = m.command[0] in ("تفعيل_الاشتراك", "fsubon")
    current = await db.get_fsub_enabled()

    if current == enable:
        state = "مفعّل" if enable else "معطّل"
        return await m.reply_text(
            f"<blockquote>⚠️ <b>الاشتراك الإجباري {state} بالفعل.</b></blockquote>"
        )

    await db.set_fsub_enabled(enable)

    if enable:
        await m.reply_text(
            "<blockquote>✅ <b>تم تفعيل الاشتراك الإجباري.</b>\n\n"
            f"لازم يكون البوت أدمن في {config.SUPPORT_CHANNEL} عشان "
            "التحقق يشتغل صح.</blockquote>"
        )
    else:
        await m.reply_text(
            "<blockquote>🚫 <b>تم تعطيل الاشتراك الإجباري.</b></blockquote>"
        )

