# ==============================================================================
# owner_panel.py - Button-based Owner Control Panel
# ==============================================================================
# One place for the bot OWNER to manage everything through inline buttons,
# reached from the "🎛️ لوحة التحكم" button on /start:
#   - Users:    see everyone who's used the bot, block/unblock with one tap
#   - Groups:   see every group the bot is in, block/unblock or leave
#   - Channels: same as groups, listed separately
#   - Broadcast: send a message to groups/channels/users you pick, with an
#                optional "pin it" toggle
#
# This reuses the exact same primitives the text commands already use
# (db.add_blacklist / app.bl_users / app.leave_chat / message.copy), just
# wrapped in buttons. Only OWNER_ID from .env can open or use this panel.
# ==============================================================================

import asyncio
from pyrogram import filters, types, errors

from tito import app, db, logger
from tito.helpers import buttons, utils

PER_PAGE = 6

# awaiting_broadcast[owner_id] = None            -> waiting for the content message
# awaiting_broadcast[owner_id] = {"message": ..., -> content received, now picking
#                                  "groups": bool,    targets/pin before sending
#                                  "channels": bool,
#                                  "users": bool,
#                                  "pin": bool}
awaiting_broadcast: dict[int, dict | None] = {}


def _owner_only(query: types.CallbackQuery) -> bool:
    return bool(query.from_user) and query.from_user.id == app.owner


async def _deny(query: types.CallbackQuery) -> None:
    await query.answer("⚠️ اللوحة دي للمالك بس.", show_alert=True)


async def _edit(target, text: str, keyboard) -> None:
    """Edit whichever message the button lives on - photo caption or plain text."""
    try:
        await target.edit_caption(caption=text, reply_markup=keyboard)
    except Exception:
        try:
            await target.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass


# ------------------------------------------------------------------------------
# MAIN PANEL
# ------------------------------------------------------------------------------

async def _render_main(target) -> None:
    users = await db.get_users()
    groups = await db.get_groups()
    channels = await db.get_channels()
    fsub_enabled = await db.get_fsub_enabled()
    text = (
        "<u><b>🎛️ لوحة تحكم المالك</b></u>\n\n"
        f"👤 عدد المستخدمين: <b>{len(users)}</b>\n"
        f"👥 عدد الجروبات: <b>{len(groups)}</b>\n"
        f"📢 عدد القنوات: <b>{len(channels)}</b>\n\n"
        "اختار اللي عاوز تتحكم فيه:"
    )
    await _edit(target, text, buttons.op_main_markup(fsub_enabled))


@app.on_callback_query(filters.regex(r"^owner_panel$"))
async def _owner_panel(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    await query.answer()
    await _render_main(query.message)


@app.on_callback_query(filters.regex(r"^op_fsub_toggle$"))
async def _op_fsub_toggle(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)

    enabled = not await db.get_fsub_enabled()
    await db.set_fsub_enabled(enabled)
    await query.answer(
        "✅ الاشتراك الإجباري اتفعّل." if enabled else "🚫 الاشتراك الإجباري اتعطّل."
    )
    await _render_main(query.message)


# ------------------------------------------------------------------------------
# USERS
# ------------------------------------------------------------------------------

async def _render_users(target, page: int) -> None:
    all_users = await db.get_users()
    start = page * PER_PAGE
    chunk = all_users[start:start + PER_PAGE]
    has_next = len(all_users) > start + PER_PAGE

    resolved = {}
    if chunk:
        try:
            fetched = await app.get_users(chunk)
            fetched = fetched if isinstance(fetched, list) else [fetched]
            resolved = {u.id: u for u in fetched}
        except Exception:
            pass

    rows_data = []
    for uid in chunk:
        u = resolved.get(uid)
        if u:
            name = u.first_name or "بدون اسم"
            if u.last_name:
                name += f" {u.last_name}"
            name = utils.esc(name)[:30]
            if u.username:
                tag = f"@{u.username}"
                chat_url = f"https://t.me/{u.username}"
            else:
                tag = f"ID: {uid}"
                chat_url = f"tg://user?id={uid}"
            label = f"{name} | {tag}"
        else:
            label = f"مستخدم محذوف | ID: {uid}"
            chat_url = f"tg://user?id={uid}"
        rows_data.append((uid, label, chat_url, uid in app.bl_users))

    text = (
        "<u><b>👤 إدارة المستخدمين</b></u>\n\n"
        f"إجمالي المستخدمين: <b>{len(all_users)}</b>\n\n"
        "اضغط على اسم أي مستخدم عشان تفتح شاته على طول، أو 🚫/✅ بجانبه عشان تحظره أو تفك حظره."
    )
    await _edit(target, text, buttons.op_users_markup(rows_data, page, has_next))


@app.on_callback_query(filters.regex(r"^op_users_\d+$"))
async def _op_users(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    await query.answer()
    page = int(query.data.rsplit("_", 1)[-1])
    await _render_users(query.message, page)


@app.on_callback_query(filters.regex(r"^op_uinfo_\d+$"))
async def _op_uinfo(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    user_id = int(query.data.replace("op_uinfo_", "", 1))
    await query.answer(f"ID: {user_id}", show_alert=True)


@app.on_callback_query(filters.regex(r"^op_utgl_\d+_\d+$"))
async def _op_utgl(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)

    _, _, user_id, page = query.data.split("_")
    user_id, page = int(user_id), int(page)

    if user_id in app.sudoers:
        return await query.answer("⚠️ متقدرش تحظر مشرف أو المالك.", show_alert=True)

    if user_id in app.bl_users:
        app.bl_users.discard(user_id)
        await db.del_blacklist(user_id)
        await query.answer("✅ اتفك الحظر.")
    else:
        app.bl_users.add(user_id)
        await db.add_blacklist(user_id)
        await query.answer("🚫 اتحظر.")

    await _render_users(query.message, page)


# ------------------------------------------------------------------------------
# GROUPS & CHANNELS (shared logic, keyed by kind "g"/"c")
# ------------------------------------------------------------------------------

async def _kind_list(kind: str) -> list[int]:
    return await db.get_groups() if kind == "g" else await db.get_channels()


async def _render_chat_list(target, kind: str, page: int) -> None:
    all_ids = await _kind_list(kind)
    start = page * PER_PAGE
    chunk = all_ids[start:start + PER_PAGE]
    has_next = len(all_ids) > start + PER_PAGE

    rows_data = []
    for cid in chunk:
        try:
            chat = await app.get_chat(cid)
            title = utils.esc(chat.title) or str(cid)
        except Exception:
            title = f"شات {cid}"
        if cid in db.blacklisted:
            title += " 🚫"
        rows_data.append((cid, title))

    label = "الجروبات" if kind == "g" else "القنوات"
    icon = "👥" if kind == "g" else "📢"
    text = (
        f"<u><b>{icon} إدارة {label}</b></u>\n\n"
        f"الإجمالي: <b>{len(all_ids)}</b>\n\n"
        "اضغط على أي واحد عشان تدير الحظر أو المغادرة."
    )
    await _edit(target, text, buttons.op_chats_markup(rows_data, kind, page, has_next))


@app.on_callback_query(filters.regex(r"^op_grp_\d+$"))
async def _op_grp(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    await query.answer()
    page = int(query.data.rsplit("_", 1)[-1])
    await _render_chat_list(query.message, "g", page)


@app.on_callback_query(filters.regex(r"^op_chn_\d+$"))
async def _op_chn(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    await query.answer()
    page = int(query.data.rsplit("_", 1)[-1])
    await _render_chat_list(query.message, "c", page)


async def _render_chat_detail(target, kind: str, chat_id: int, page: int) -> None:
    try:
        chat = await app.get_chat(chat_id)
        title = utils.esc(chat.title)
        username = f"@{chat.username}" if chat.username else "خاص"
    except Exception:
        title = f"شات {chat_id}"
        username = "-"

    blocked = chat_id in db.blacklisted
    status = "🚫 محظور (البوت هيسيبه ويتوقف فيه)" if blocked else "✅ شغال عادي"

    text = (
        f"<u><b>{title}</b></u>\n\n"
        f"🆔 <code>{chat_id}</code>\n"
        f"👤 {username}\n"
        f"الحالة: {status}"
    )
    await _edit(target, text, buttons.op_chat_detail_markup(kind, chat_id, page, blocked))


@app.on_callback_query(filters.regex(r"^op_cd_[gc]_-?\d+_\d+$"))
async def _op_chat_detail(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    await query.answer()
    _, _, kind, chat_id, page = query.data.split("_")
    await _render_chat_detail(query.message, kind, int(chat_id), int(page))


@app.on_callback_query(filters.regex(r"^op_cblk_[gc]_-?\d+_\d+$"))
async def _op_chat_toggle_block(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)

    _, _, kind, chat_id, page = query.data.split("_")
    chat_id, page = int(chat_id), int(page)

    if chat_id in db.blacklisted:
        await db.del_blacklist(chat_id)
        await query.answer("✅ اتفك الحظر عن الشات.")
    else:
        await db.add_blacklist(chat_id)
        await query.answer("🚫 اتحظر الشات.")

    await _render_chat_detail(query.message, kind, chat_id, page)


@app.on_callback_query(filters.regex(r"^op_clv_[gc]_-?\d+_\d+$"))
async def _op_chat_leave_confirm(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    await query.answer()
    _, _, kind, chat_id, page = query.data.split("_")
    chat_id, page = int(chat_id), int(page)

    text = "⚠️ متأكد إنك عاوز البوت يسيب الشات ده؟"
    await _edit(query.message, text, buttons.op_leave_confirm_markup(kind, chat_id, page))


@app.on_callback_query(filters.regex(r"^op_clvy_[gc]_-?\d+_\d+$"))
async def _op_chat_leave_execute(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)

    _, _, kind, chat_id, page = query.data.split("_")
    chat_id, page = int(chat_id), int(page)

    # Assistant/userbot leaves too, if it's in there (mirrors /leave)
    try:
        client = await db.get_client(chat_id)
        if client:
            try:
                await client.leave_chat(chat_id)
            except Exception:
                pass
    except Exception:
        pass

    try:
        await app.leave_chat(chat_id)
        await query.answer("👋 خرج بنجاح.")
    except Exception as e:
        await query.answer(f"❌ فشل: {e}", show_alert=True)

    await db.rm_chat(chat_id)
    await _render_chat_list(query.message, kind, page)


# ------------------------------------------------------------------------------
# BROADCAST
# ------------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^op_bc$"))
async def _op_bc_start(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    await query.answer()
    awaiting_broadcast[query.from_user.id] = None
    text = (
        "<u><b>📣 الإذاعة</b></u>\n\n"
        "ابعتلي دلوقتي الرسالة اللي عاوز تذيعها (نص، صورة، فيديو، صوت، أي حاجة)."
    )
    await _edit(query.message, text, buttons.op_broadcast_cancel_markup())


@app.on_callback_query(filters.regex(r"^op_bc_abort$"))
async def _op_bc_abort(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)
    awaiting_broadcast.pop(query.from_user.id, None)
    await query.answer("تم الإلغاء.")
    await _render_main(query.message)


def _pending_broadcast_content_filter(_, __, message: types.Message) -> bool:
    return (
        bool(message.chat)
        and message.chat.type.name == "PRIVATE"
        and bool(message.from_user)
        and message.from_user.id == app.owner
        and message.from_user.id in awaiting_broadcast
        and awaiting_broadcast[message.from_user.id] is None
    )


pending_broadcast_content = filters.create(_pending_broadcast_content_filter)


@app.on_message(pending_broadcast_content)
async def _op_bc_receive(_, m: types.Message):
    awaiting_broadcast[m.from_user.id] = {
        "message": m,
        "groups": True,
        "channels": True,
        "users": False,
        "pin": False,
    }
    text = (
        "<u><b>📣 حدد وين تبعت الرسالة</b></u>\n\n"
        "اضغط عشان تفعّل/تلغي أي خيار، وبعدين دوس \"ابعت دلوقتي\"."
    )
    await m.reply_text(
        text,
        reply_markup=buttons.op_broadcast_targets_markup(True, True, False, False),
    )


@app.on_callback_query(filters.regex(r"^op_bc_tgl_[gcup]$"))
async def _op_bc_toggle(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)

    state = awaiting_broadcast.get(query.from_user.id)
    if not state:
        return await query.answer("⏱️ الجلسة انتهت، ابدأ تاني من زر الإذاعة.", show_alert=True)

    key_map = {"g": "groups", "c": "channels", "u": "users", "p": "pin"}
    key = key_map[query.data[-1]]
    state[key] = not state[key]
    await query.answer()

    try:
        await query.message.edit_reply_markup(
            reply_markup=buttons.op_broadcast_targets_markup(
                state["groups"], state["channels"], state["users"], state["pin"]
            )
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^op_bc_send$"))
async def _op_bc_send(_, query: types.CallbackQuery):
    if not _owner_only(query):
        return await _deny(query)

    state = awaiting_broadcast.get(query.from_user.id)
    if not state:
        return await query.answer("⏱️ الجلسة انتهت، ابدأ تاني من زر الإذاعة.", show_alert=True)

    if not (state["groups"] or state["channels"] or state["users"]):
        return await query.answer("⚠️ اختار وجهة واحدة على الأقل.", show_alert=True)

    awaiting_broadcast.pop(query.from_user.id, None)
    await query.answer()

    targets: list[int] = []
    if state["groups"]:
        targets += await db.get_groups()
    if state["channels"]:
        targets += await db.get_channels()
    if state["users"]:
        targets += await db.get_users()

    src_message: types.Message = state["message"]
    pin = state["pin"]

    status = await query.message.edit_text(f"🚀 جاري الإذاعة لـ {len(targets)}...")

    sent_count = 0
    pinned_count = 0
    failed = 0

    for chat_id in targets:
        try:
            copied = await src_message.copy(chat_id)
            sent_count += 1
            if pin:
                try:
                    await copied.pin(disable_notification=True)
                    pinned_count += 1
                except Exception as pin_ex:
                    logger.warning(
                        f"Owner-panel broadcast: pin failed for {chat_id} "
                        f"({type(pin_ex).__name__}: {pin_ex})")

        except errors.FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
            try:
                copied = await src_message.copy(chat_id)
                sent_count += 1
                if pin:
                    try:
                        await copied.pin(disable_notification=True)
                        pinned_count += 1
                    except Exception as pin_ex:
                        logger.warning(
                            f"Owner-panel broadcast: pin retry failed for {chat_id} "
                            f"({type(pin_ex).__name__}: {pin_ex})")
            except Exception:
                failed += 1

        except errors.ChannelPrivate:
            failed += 1
            try:
                await db.rm_chat(chat_id)
            except Exception:
                pass

        except (errors.UserIsBlocked, errors.PeerIdInvalid, errors.ChatWriteForbidden):
            failed += 1

        except Exception:
            failed += 1

        await asyncio.sleep(0.3)  # anti-flood delay, same as the text /broadcast command

    await status.edit_text(
        "<u><b>✅ خلصت الإذاعة</b></u>\n\n"
        f"اتبعتت لـ: <b>{sent_count}</b>\n"
        f"اتثبتت في: <b>{pinned_count}</b>\n"
        f"فشلت: <b>{failed}</b>"
    )
