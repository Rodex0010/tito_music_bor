# ==============================================================================
# azan.py - Azan (prayer time) settings
# ==============================================================================
# تفعيل_الاذان <المدينة> <الدولة>  -> enables the azan for this chat
# تعطيل_الاذان                    -> disables it
# ==============================================================================

from pyrogram import filters, types

from tito import app, db


async def _is_group_admin(message: types.Message) -> bool:
    if not message.from_user:
        return False
    if message.from_user.id in app.sudoers:
        return True
    member = await app.get_chat_member(message.chat.id, message.from_user.id)
    return bool(
        (member.privileges and member.privileges.can_manage_video_chats)
        or "OWNER" in str(member.status)
        or "ADMINISTRATOR" in str(member.status)
    )


@app.on_message(filters.command(["تفعيل_الاذان", "azanon"], prefixes=["", "/"]) & filters.group)
async def enable_azan(_, message: types.Message):
    if not await _is_group_admin(message):
        return await message.reply_text("🚫 هذا الأمر للأدمنز فقط.")

    if len(message.command) < 3:
        return await message.reply_text(
            "<b>الاستخدام:</b>\n<code>تفعيل_الاذان المدينة الدولة</code>\n"
            "مثال: <code>تفعيل_الاذان Cairo Egypt</code>"
        )

    city = message.command[1]
    country = message.command[2]

    await db.set_azan(message.chat.id, enabled=True, city=city, country=country)

    await message.reply_text(
        f"✅ تم تفعيل الأذان لهذه المجموعة.\n"
        f"📍 المدينة: <b>{city}</b> - الدولة: <b>{country}</b>\n"
        f"سيقوم البوت تلقائيًا بفتح الكول والإعلان عن كل أذان في وقته."
    )


@app.on_message(filters.command(["تعطيل_الاذان", "azanoff"], prefixes=["", "/"]) & filters.group)
async def disable_azan(_, message: types.Message):
    if not await _is_group_admin(message):
        return await message.reply_text("🚫 هذا الأمر للأدمنز فقط.")

    doc = await db.get_azan(message.chat.id)
    await db.set_azan(
        message.chat.id,
        enabled=False,
        city=doc.get("city") if doc else None,
        country=doc.get("country") if doc else None,
    )
    await message.reply_text("🔕 تم تعطيل الأذان لهذه المجموعة.")
