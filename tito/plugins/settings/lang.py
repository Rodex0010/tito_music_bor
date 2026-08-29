# ==============================================================================
# lang.py - Language Configuration
# ==============================================================================
# Lets each private chat pick its own language, and lets group admins change
# the language for the whole group.
# ==============================================================================

import html

from pyrogram import filters, types, enums
from pyrogram.enums import ChatType
from tito import app, db, lang
from tito.core.lang import lang_codes
from tito.helpers import buttons


@app.on_message(filters.command(["لغة", "اللغة", "lang", "language"], prefixes=["", "/"]) & ~app.bl_users)
@lang.language()
async def set_lang_command(_, message: types.Message):
    # Auto-delete command message
    try:
        await message.delete()
    except Exception:
        pass

    if message.chat.type != ChatType.PRIVATE:
        # Group admins/owner/sudo only
        member = await app.get_chat_member(message.chat.id, message.from_user.id)
        if not (
            (member.privileges and member.privileges.can_manage_video_chats)
            or message.from_user.id in app.sudoers
            or "OWNER" in str(member.status)
            or "ADMINISTRATOR" in str(member.status)
        ):
            return await message.reply_text(message.lang["lang_admin_only"])

    keyboard = buttons.lang_markup(message.lang, back_target="help")

    await message.reply_text(
        text=message.lang["lang_menu_title"],
        reply_markup=keyboard,
        quote=False
    )


@app.on_callback_query(filters.regex(r"^lang_menu$") & ~app.bl_users)
@lang.language()
async def lang_menu_callback(_, query: types.CallbackQuery):
    """Open the language picker from the start/help menus."""
    await query.answer()
    keyboard = buttons.lang_markup(query.lang, back_target="start")

    try:
        await query.edit_message_caption(
            caption=query.lang["lang_menu_title"],
            reply_markup=keyboard,
        )
    except Exception:
        try:
            await query.edit_message_text(
                text=query.lang["lang_menu_title"],
                reply_markup=keyboard,
            )
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^set_lang_") & ~app.bl_users)
@lang.language()
async def set_lang_callback(client, query: types.CallbackQuery):
    try:
        parts = query.data.split("_")
        # set_lang_{code}_{back_target}
        lang_code = parts[2]
        back_target = "_".join(parts[3:]) or "start"
    except IndexError:
        return await query.answer("بيانات الزر غير صالحة.", show_alert=True)

    if lang_code not in lang_codes:
        return await query.answer("هذه اللغة غير مدعومة.", show_alert=True)

    # In groups, only admins/owner/sudo may change the shared group language.
    # In private chats, everyone may set their own personal language.
    if query.message.chat.type != ChatType.PRIVATE:
        try:
            member = await client.get_chat_member(query.message.chat.id, query.from_user.id)
        except Exception:
            return await query.answer("تعذر التحقق من الصلاحيات.", show_alert=True)

        is_privileged = (
            (member.privileges and member.privileges.can_manage_video_chats)
            or query.from_user.id in app.sudoers
            or "OWNER" in str(member.status)
            or "ADMINISTRATOR" in str(member.status)
        )
        if not is_privileged:
            return await query.answer(query.lang["lang_admin_only"], show_alert=True)

    # Save to database
    await db.set_lang(query.message.chat.id, lang_code)

    # Inform user
    try:
        lang_name = lang_codes[lang_code]
        # Re-fetch new translation dict for immediate effect in this callback
        new_lang_dict = await lang.get_lang(query.message.chat.id)

        # Show the success message as a toast instead of wiping the panel,
        # then rebuild whichever menu we came from (start/help) so the
        # keyboard stays intact instead of disappearing.
        await query.answer(new_lang_dict["lang_success"].format(lang_name), show_alert=False)

        private = query.message.chat.type == ChatType.PRIVATE

        if back_target == "help":
            _text = new_lang_dict["help_menu"]
            keyboard = buttons.help_markup(new_lang_dict)
        else:  # back_target == "start" (or anything else, default to start)
            # Escaped - see the matching note in plugins/info/start.py
            _text = (
                new_lang_dict["start_pm"].format(
                    html.escape(query.from_user.first_name), html.escape(app.name)
                )
                if private
                else new_lang_dict["start_gp"].format(html.escape(app.name))
            )
            keyboard = buttons.start_key(
                new_lang_dict, private, is_owner=private and query.from_user.id == app.owner
            )

        try:
            await query.message.edit_caption(
                caption=_text,
                reply_markup=keyboard,
            )
        except Exception:
            await query.message.edit_text(
                text=_text,
                reply_markup=keyboard,
            )
    except Exception:
        await query.answer("فشل تحديث الرسالة.", show_alert=True)
