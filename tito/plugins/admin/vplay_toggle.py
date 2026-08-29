# ============================================================================== 
# vplay_toggle.py - Video Play Toggle
# ============================================================================== 
# Globally turn /vplay on or off without restarting the bot.
# ============================================================================== 

from pyrogram import filters, types

from tito import app, db, lang


@app.on_message(filters.command(["تفعيل_الفيديو", "تعطيل_الفيديو", "vplayon", "vplayoff"], prefixes=["", "/"]) & app.sudo_filter)
@lang.language()
async def _toggle_vplay(_, m: types.Message):
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass

    enable = m.command[0] == "تفعيل_الفيديو"
    current = await db.get_vplay_enabled()

    if current == enable:
        state = "مفعّل" if enable else "معطّل"
        return await m.reply_text(
            f"<blockquote>⚠️ <b>تشغيل الفيديو (شغل_فيديو) {state} بالفعل.</b></blockquote>"
        )

    await db.set_vplay_enabled(enable)

    if enable:
        await m.reply_text(
            "<blockquote>✅ <b>تم تفعيل تشغيل الفيديو (شغل_فيديو) بشكل عام.</b></blockquote>"
        )
    else:
        await m.reply_text(
            "<blockquote>🚫 <b>تم تعطيل تشغيل الفيديو (شغل_فيديو) بشكل عام.</b></blockquote>"
        )
