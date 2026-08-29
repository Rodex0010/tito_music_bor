# ==============================================================================
# autoleave.py - Auto-Leave
# ==============================================================================
# Sudo command to toggle the auto-leave feature on or off. 
# If on, the assistant drops out of VC after 5 minutes of inactivity.
# ==============================================================================

from pyrogram import filters
from pyrogram.types import Message

from tito import app, db


@app.on_message(
    filters.command(["مغادرة_تلقائية", "autoleave"], prefixes=["", "/"])
    & (filters.group | filters.channel)
    & ~app.bl_users
)
async def autoleave_command(_, m: Message) -> None:
    
    # Check if user is sudo user
    if m.from_user.id not in app.sudoers:
        return await m.reply_text(
            "❌ ᴏɴʟʏ ꜱᴜᴅᴏ ᴜꜱᴇʀꜱ ᴄᴀɴ ᴜꜱᴇ ᴛʜɪꜱ ᴄᴏᴍᴍᴀɴᴅ."
        )
    
    # Check if subcommand is provided
    if len(m.command) < 2:
        current_status = await db.get_autoleave(m.chat.id)
        status_text = "مفعّلة" if current_status else "معطّلة"
        return await m.reply_text(
            f"<blockquote>🔧 حالة المغادرة التلقائية: {status_text}</blockquote>\n\n"
            "<blockquote><b>طريقة الاستخدام:</b>\n"
            "• `مغادرة_تلقائية تفعيل` - تفعيل المغادرة التلقائية\n"
            "• `مغادرة_تلقائية تعطيل` - تعطيل المغادرة التلقائية</blockquote>\n\n"
            "<blockquote><i>عند التفعيل، سيغادر المساعد المكالمة الصوتية بعد 5 دقائق "
            "إذا لم يكن هناك أحد يستمع.</i></blockquote>"
        )
    
    subcommand = m.command[1].lower()
    
    if subcommand in ("تفعيل", "enable"):
        await db.set_autoleave(m.chat.id, True)
        await m.reply_text(
            "✅ <blockquote>تم تفعيل المغادرة التلقائية!</blockquote>\n\n"
            "<blockquote>سيغادر المساعد المكالمة الصوتية بعد <b>5 دقائق</b> "
            "إذا لم يكن هناك أحد يستمع.</blockquote>"
        )
    elif subcommand in ("تعطيل", "disable"):
        await db.set_autoleave(m.chat.id, False)
        await m.reply_text(
            "✅ <blockquote>تم تعطيل المغادرة التلقائية!</blockquote>\n\n"
            "<blockquote>سيبقى المساعد في المكالمة الصوتية حتى لو لم يكن هناك أحد يستمع.</blockquote>"
        )
    else:
        await m.reply_text(
            "❌ <blockquote>أمر فرعي غير صحيح!</blockquote>\n\n"
            "<blockquote><b>طريقة الاستخدام:</b>\n"
            "• `مغادرة_تلقائية تفعيل`\n"
            "• `مغادرة_تلقائية تعطيل`</blockquote>"
        )
