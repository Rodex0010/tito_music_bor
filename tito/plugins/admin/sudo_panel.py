# ==============================================================================
# sudo_panel.py - Button-based Sudo Management (Owner Only)
# ==============================================================================
# Lets the bot OWNER add/remove sudo admins entirely through inline buttons,
# from the "👑 إدارة المشرفين" button on the /start panel. No text commands
# needed. Only the OWNER_ID from .env can open or use this panel.
# ==============================================================================

from pyrogram import filters, types

from tito import app, db
from tito.helpers import buttons

# Tracks which owner chat is currently waiting to receive a user to add.
# (Only ever contains app.owner, but kept as a set for safety/clarity.)
awaiting_add: set[int] = set()


async def _build_panel_text() -> str:
    sudoers = await db.get_sudoers()
    others = [u for u in sudoers if u != app.owner]

    text = "<u><b>👑 إدارة المشرفين (السودو)</b></u>\n\n"
    if not others:
        text += "لا يوجد أي مشرف مضاف حاليًا غير المالك.\n\n"
    else:
        text += "هؤلاء هم المصرح لهم حاليًا كمشرفين عامين للبوت:\n<blockquote>"
        for user_id in others:
            try:
                user = await app.get_users(user_id)
                text += f"\n- {user.mention} (<code>{user_id}</code>)"
            except Exception:
                text += f"\n- حساب محذوف (<code>{user_id}</code>)"
        text += "\n</blockquote>\n\n"
    text += "اضغط ➕ لإضافة مشرف جديد، أو ➖ بجانب أي مشرف لإزالته."
    return text


async def _refresh_panel(target) -> None:
    """Edit the given message (or callback query message) to show the panel."""
    sudoers = await db.get_sudoers()
    text = await _build_panel_text()
    keyboard = buttons.sudo_panel_markup(sudoers, app.owner)
    try:
        await target.edit_caption(caption=text, reply_markup=keyboard)
    except Exception:
        try:
            await target.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^sudo_panel$"))
async def _sudo_panel(_, query: types.CallbackQuery):
    if query.from_user.id != app.owner:
        return await query.answer("⚠️ هذه اللوحة للمالك فقط.", show_alert=True)

    await query.answer()
    await _refresh_panel(query.message)


@app.on_callback_query(filters.regex(r"^sudo_add$"))
async def _sudo_add_prompt(_, query: types.CallbackQuery):
    if query.from_user.id != app.owner:
        return await query.answer("⚠️ هذه اللوحة للمالك فقط.", show_alert=True)

    await query.answer()
    awaiting_add.add(query.from_user.id)

    text = (
        "<u><b>➕ إضافة مشرف جديد</b></u>\n\n"
        "ابعتلي دلوقتي أي حاجة من دول عشان أضيف الشخص كمشرف:\n"
        "• آيدي المستخدم الرقمي (مثال: <code>123456789</code>)\n"
        "• يوزر المستخدم (مثال: <code>@username</code>)\n"
        "• أو حوّل (Forward) رسالة منه\n\n"
        "أو اضغط إلغاء للرجوع."
    )
    keyboard = buttons.ikm(
        [[buttons.ikb(text="❌ إلغاء", callback_data="sudo_add_cancel")]]
    )
    try:
        await query.message.edit_caption(caption=text, reply_markup=keyboard)
    except Exception:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^sudo_add_cancel$"))
async def _sudo_add_cancel(_, query: types.CallbackQuery):
    if query.from_user.id != app.owner:
        return await query.answer("⚠️ هذه اللوحة للمالك فقط.", show_alert=True)

    awaiting_add.discard(query.from_user.id)
    await query.answer("تم الإلغاء.")
    await _refresh_panel(query.message)


@app.on_callback_query(filters.regex(r"^sudo_rm_"))
async def _sudo_remove(_, query: types.CallbackQuery):
    if query.from_user.id != app.owner:
        return await query.answer("⚠️ هذه اللوحة للمالك فقط.", show_alert=True)

    try:
        target_id = int(query.data.replace("sudo_rm_", "", 1))
    except ValueError:
        return await query.answer("بيانات غير صالحة.", show_alert=True)

    if target_id == app.owner:
        return await query.answer("لا يمكن إزالة المالك.", show_alert=True)

    if target_id not in app.sudoers:
        await query.answer("هذا المستخدم ليس مشرفًا بالفعل.", show_alert=True)
        return await _refresh_panel(query.message)

    app.sudoers.discard(target_id)
    app.sudo_filter.update([])
    app.sudo_filter.update(app.sudoers)
    await db.del_sudo(target_id)

    await query.answer("✅ تم إزالة المشرف.")
    await _refresh_panel(query.message)


def _pending_owner_filter(_, __, message: types.Message) -> bool:
    return (
        message.chat
        and message.chat.type.name == "PRIVATE"
        and bool(message.from_user)
        and message.from_user.id == app.owner
        and message.from_user.id in awaiting_add
    )


pending_owner_add = filters.create(_pending_owner_filter)


async def _extract_candidate(m: types.Message) -> types.User | None:
    # Forwarded message from the target user
    if m.forward_from:
        return m.forward_from

    # Text-mention entity (clickable mention of a user without username)
    if m.entities:
        for e in m.entities:
            if e.type.name == "TEXT_MENTION" and e.user:
                return e.user

    if m.text:
        text = m.text.strip()
        # @username
        if text.startswith("@"):
            try:
                return await app.get_users(text)
            except Exception:
                return None
        # Numeric ID
        if text.lstrip("-").isdigit():
            try:
                return await app.get_users(int(text))
            except Exception:
                return None

    return None


@app.on_message(pending_owner_add)
async def _sudo_add_receive(_, m: types.Message):
    awaiting_add.discard(m.from_user.id)

    user = await _extract_candidate(m)
    try:
        await m.delete()
    except Exception:
        pass

    if not user:
        sent = await app.send_message(
            m.chat.id,
            "❌ مقدرتش أتعرف على المستخدم ده. جرب تاني بآيدي رقمي، يوزر، أو تحويل رسالة منه.",
        )
        return await _refresh_panel_via_new(sent)

    if user.id in app.sudoers:
        sent = await app.send_message(
            m.chat.id, f"⚠️ {user.mention} مشرف بالفعل.",
        )
        return await _refresh_panel_via_new(sent)

    app.sudoers.add(user.id)
    app.sudo_filter.update([user.id])
    await db.add_sudo(user.id)

    sent = await app.send_message(m.chat.id, f"✅ تم إضافة {user.mention} كمشرف بنجاح.")
    await _refresh_panel_via_new(sent)


async def _refresh_panel_via_new(status_message: types.Message) -> None:
    """After a plain text confirmation, send the updated panel as a fresh message
    (there's no photo/panel message left to edit at this point)."""
    sudoers = await db.get_sudoers()
    text = await _build_panel_text()
    keyboard = buttons.sudo_panel_markup(sudoers, app.owner)
    await app.send_message(status_message.chat.id, text, reply_markup=keyboard)
