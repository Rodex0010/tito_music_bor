# ==============================================================================
# session_manager.py - "🔄 تحديث الجلسات" Session Refresh Panel
# ==============================================================================
# Lets the OWNER, and any admin the owner explicitly grants access to, replace
# an assistant's STRING_SESSION without touching the server. There are two
# ways to set/replace an assistant's session:
#
#   1) 🔄 تحديث  - the bot logs the account in itself:
#        pick assistant -> 🔄 تحديث -> send phone number
#        -> send the login code Telegram sent you
#        -> (send 2FA password, only if the account has one)
#
#   2) 📋 لصق جلسة جاهزة - someone ALREADY has a Pyrogram string session
#      (e.g. generated themselves with a tool like
#      https://telegram.tools/session-string-generator#pyrogram,user) and
#      just pastes it to the bot directly - no phone/code needed here at
#      all. The bot only validates it works before saving it.
#
# On success (either path):
#   1. The OLD session is logged out (log_out()), so it can never be reused -
#      this is what stops the account from ever having two live logins at
#      once, which is what gets accounts flagged/limited by Telegram.
#   2. The NEW session replaces it live (no restart needed) and is saved to
#      the DB so it survives one (see userbot.sync_overrides()).
#
# Who is ALLOWED to open this panel and submit a session at all:
#   - The owner, always.
#   - Any admin in db.session_admins - toggled by the owner from
#     🔄 تحديث الجلسات -> 👥 صلاحيات التحديث.
#   Nobody outside that list can trigger these handlers - the _allowed()
#   check below runs before every single callback and message handler here.
# ==============================================================================

from pyrogram import Client, errors, filters, types

from tito import app, config, db, logger, userbot
from tito.helpers import buttons

PER_PAGE = 6

SLOT = {1: "one", 2: "two", 3: "three"}

# pending[user_id] = {
#   "num": int,               assistant slot being refreshed
#   "stage": "phone"|"code"|"password"|"paste_string",
#   "client": Client,         ephemeral, in-memory login/validation client
#   "phone": str,
#   "phone_code_hash": str,
#   "session_string": str,    only set on the "paste_string" path
#   "panel_msg": Message,     the panel message we keep editing
# }
pending: dict[int, dict] = {}


# ------------------------------------------------------------------------------
# Permission
# ------------------------------------------------------------------------------

async def _allowed(user_id: int) -> bool:
    if user_id == app.owner:
        return True
    return user_id in await db.get_session_admins()


async def _deny(query: types.CallbackQuery) -> None:
    await query.answer("⚠️ مالكش صلاحية تستخدم لوحة تحديث الجلسات دي.", show_alert=True)


async def _edit(target, text: str, keyboard) -> None:
    try:
        await target.edit_text(text=text, reply_markup=keyboard)
    except Exception:
        try:
            await target.edit_caption(caption=text, reply_markup=keyboard)
        except Exception:
            pass


def _assistant_label(num: int) -> str:
    client = getattr(userbot, SLOT[num], None)
    if client is not None and getattr(client, "is_connected", False):
        name = getattr(client, "name", None) or f"Assistant {num}"
        username = getattr(client, "username", None)
        return f"{name} (@{username})" if username else str(name)
    return f"Assistant {num} (مش متصل حاليًا)"


def _configured_assistants() -> list[tuple[int, str]]:
    out = []
    for num in (1, 2, 3):
        if getattr(config, f"SESSION{num}", ""):
            out.append((num, _assistant_label(num)))
    return out


async def _cleanup(user_id: int) -> None:
    state = pending.pop(user_id, None)
    if state and state.get("client"):
        try:
            await state["client"].disconnect()
        except Exception:
            pass


# ------------------------------------------------------------------------------
# PANEL: list -> detail
# ------------------------------------------------------------------------------

async def _render_list(target, user_id: int) -> None:
    assistants = _configured_assistants()
    if not assistants:
        text = "<u><b>🔄 تحديث الجلسات</b></u>\n\nمفيش أي أسستنت متظبط في الإعدادات (STRING_SESSION)."
    else:
        text = (
            "<u><b>🔄 تحديث الجلسات</b></u>\n\n"
            "اختار الحساب اللي عاوز تحدّث جلسته:"
        )
    await _edit(target, text, buttons.sess_list_markup(assistants, user_id == app.owner))


@app.on_callback_query(filters.regex(r"^sess_panel$"))
async def _sess_panel(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    await query.answer()
    await _render_list(query.message, query.from_user.id)


@app.on_callback_query(filters.regex(r"^sess_add$"))
async def _sess_add(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    await query.answer()
    text = (
        "<u><b>➕ اضافة جلسة</b></u>\n\n"
        "اختار السلوت اللي عاوز تسجل دخول أسستنت جديد فيه:"
    )
    await _edit(query.message, text, buttons.sess_add_markup())


@app.on_callback_query(filters.regex(r"^sess_view_[123]$"))
async def _sess_view(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    await query.answer()
    num = int(query.data.rsplit("_", 1)[-1])
    configured = bool(getattr(config, f"SESSION{num}", ""))
    if configured:
        text = (
            f"<u><b>👤 {_assistant_label(num)}</b></u>\n\n"
            "دوس \"تحديث\" لو عاوز تسجّل دخول جديد للحساب ده.\n"
            "الجلسة القديمة هتتلغي أوتوماتيك بعد ما الجلسة الجديدة تتظبط، عشان الحساب ميتجمدش."
        )
    else:
        text = (
            f"<u><b>👤 Assistant {num}</b></u>\n\n"
            "السلوت ده فاضي دلوقتي. دوس \"➕ تسجيل دخول\" وابعتلي رقم الحساب اللي عاوز تضيفه."
        )
    await _edit(query.message, text, buttons.sess_detail_markup(num, configured))


# ------------------------------------------------------------------------------
# REFRESH FLOW: phone -> code -> (password) -> swap
# ------------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^sess_refresh_[123]$"))
async def _sess_refresh_start(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    num = int(query.data.rsplit("_", 1)[-1])
    await query.answer()

    await _cleanup(query.from_user.id)
    pending[query.from_user.id] = {
        "num": num,
        "stage": "phone",
        "client": None,
        "panel_msg": query.message,
    }
    text = (
        f"<u><b>🔄 تحديث جلسة: {_assistant_label(num)}</b></u>\n\n"
        "ابعتلي رقم الهاتف بتاع الحساب ده بالصيغة الدولية، مثلا:\n"
        "<code>+201234567890</code>"
    )
    await _edit(query.message, text, buttons.sess_login_cancel_markup(num))


@app.on_callback_query(filters.regex(r"^sess_paste_[123]$"))
async def _sess_paste_start(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    num = int(query.data.rsplit("_", 1)[-1])
    await query.answer()

    await _cleanup(query.from_user.id)
    pending[query.from_user.id] = {
        "num": num,
        "stage": "paste_string",
        "client": None,
        "panel_msg": query.message,
    }
    text = (
        f"<u><b>📋 لصق جلسة جاهزة: {_assistant_label(num)}</b></u>\n\n"
        "ابعتلي الـ Pyrogram String Session بتاعة الحساب اللي عاوز تضيفه كأسستنت.\n\n"
        "لو معندكش واحدة جاهزة، استخرجها من هنا:\n"
        "https://telegram.tools/session-string-generator#pyrogram,user\n\n"
        "⚠️ الجلسة دي بتدّي وصول كامل للحساب، ابعتها هنا بس ولحد تثق فيه."
    )
    await _edit(query.message, text, buttons.sess_login_cancel_markup(num))


@app.on_callback_query(filters.regex(r"^sess_delete_[123]$"))
async def _sess_delete_ask(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    await query.answer()
    num = int(query.data.rsplit("_", 1)[-1])
    text = (
        f"<u><b>🗑 مسح جلسة: {_assistant_label(num)}</b></u>\n\n"
        "متأكد؟ الحساب ده هيتسجل خروج (log out) فورًا، والجلسة هتتمسح خالص من "
        "قاعدة البيانات - مش هترجع تاني غير لو سجلت دخول جديد من \"🔄 تحديث\".\n\n"
        "الخطوة دي عشان الجلسة متفضلش عالقة/متجمدة لو فيها مشكلة."
    )
    await _edit(query.message, text, buttons.sess_delete_confirm_markup(num))


@app.on_callback_query(filters.regex(r"^sess_delete_yes_[123]$"))
async def _sess_delete_do(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    num = int(query.data.rsplit("_", 1)[-1])
    await query.answer("🗑 بتتمسح...")

    # If this same user (or anyone) had a login flow open on this slot,
    # kill it first so we don't end up with a half-finished pending state
    # pointing at a client we're about to remove.
    for uid, state in list(pending.items()):
        if state.get("num") == num:
            await _cleanup(uid)

    try:
        await userbot.remove_client(num)
    except Exception as e:
        logger.warning(f"Couldn't cleanly remove assistant {num} session: {e}")

    await db.del_session_override(num)

    text = (
        f"<u><b>✅ اتمسحت جلسة: Assistant {num}</b></u>\n\n"
        "الحساب سجل خروج والجلسة اتشالت خالص، مفيش حاجة هتفضل عالقة.\n"
        "لو عاوز تفعّل الأسستنت ده تاني، دوس \"➕ تسجيل دخول\" وسجل دخول جديد."
    )
    await _edit(query.message, text, buttons.sess_detail_markup(num, configured=False))


@app.on_callback_query(filters.regex(r"^sess_cancel_[123]$"))
async def _sess_cancel(_, query: types.CallbackQuery):
    if not await _allowed(query.from_user.id):
        return await _deny(query)
    num = int(query.data.rsplit("_", 1)[-1])
    await _cleanup(query.from_user.id)
    await query.answer("تم الإلغاء.")
    configured = bool(getattr(config, f"SESSION{num}", ""))
    text = f"<u><b>👤 {_assistant_label(num)}</b></u>"
    await _edit(query.message, text, buttons.sess_detail_markup(num, configured))


@app.on_callback_query(filters.regex(r"^sess_noop$"))
async def _sess_noop(_, query: types.CallbackQuery):
    await query.answer()


def _pending_stage_filter(stage: str):
    def fn(_, __, message: types.Message) -> bool:
        return (
            bool(message.chat)
            and message.chat.type.name == "PRIVATE"
            and bool(message.from_user)
            and message.from_user.id in pending
            and pending[message.from_user.id]["stage"] == stage
        )
    return filters.create(fn)


async def _finalize(user_id: int) -> None:
    state = pending[user_id]
    num = state["num"]
    tmp: Client = state["client"]
    panel_msg = state["panel_msg"]

    new_session = await tmp.export_session_string()
    try:
        await tmp.disconnect()
    except Exception:
        pass

    # Log out the OLD session first, so the account never has both the old
    # and new session alive at the same time (that's what trips bans/limits).
    old_client = getattr(userbot, SLOT[num], None)
    if old_client is not None:
        try:
            if not old_client.is_connected:
                await old_client.connect()
            await old_client.log_out()
        except Exception as e:
            logger.warning(f"Couldn't cleanly log out old assistant {num} session: {e}")

    # Bring the new session online in its place and persist it.
    await userbot.replace_client(num, new_session)
    await db.set_session_override(num, new_session)

    pending.pop(user_id, None)

    text = (
        f"<u><b>✅ اتحدثت جلسة: {_assistant_label(num)}</b></u>\n\n"
        "الجلسة القديمة اتلغت والجديدة شغالة دلوقتي بدون ما تعمل ريستارت للبوت."
    )
    await _edit(panel_msg, text, buttons.sess_detail_markup(num))


async def _finalize_pasted(user_id: int, new_session: str) -> None:
    """Same swap-in logic as _finalize(), but the session string was pasted
    directly by the user instead of produced by an in-bot phone/code login."""
    state = pending[user_id]
    num = state["num"]
    panel_msg = state["panel_msg"]

    # Log out the OLD session first, so the account never has both the old
    # and new session alive at the same time (that's what trips bans/limits).
    old_client = getattr(userbot, SLOT[num], None)
    if old_client is not None:
        try:
            if not old_client.is_connected:
                await old_client.connect()
            await old_client.log_out()
        except Exception as e:
            logger.warning(f"Couldn't cleanly log out old assistant {num} session: {e}")

    # Bring the new session online in its place and persist it.
    await userbot.replace_client(num, new_session)
    await db.set_session_override(num, new_session)

    pending.pop(user_id, None)

    text = (
        f"<u><b>✅ اتضافت جلسة: {_assistant_label(num)}</b></u>\n\n"
        "الجلسة اللي بعتهالي اتظبطت وشغالة دلوقتي بدون ما تعمل ريستارت للبوت."
    )
    await _edit(panel_msg, text, buttons.sess_detail_markup(num))


@app.on_message(_pending_stage_filter("paste_string"))
async def _sess_receive_pasted(_, m: types.Message):
    if not m.text:
        return await m.reply_text("⚠️ ابعت الجلسة كنص.")
    state = pending[m.from_user.id]
    session_string = m.text.strip()
    try:
        await m.delete()
    except Exception:
        pass

    # Validate the pasted string actually works before touching anything
    # live - connect a throwaway client with it and confirm we can log in.
    tmp = Client(
        name=f"sess_paste_{m.from_user.id}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    try:
        await tmp.connect()
        me = await tmp.get_me()
    except Exception as e:
        try:
            await tmp.disconnect()
        except Exception:
            pass
        return await state["panel_msg"].edit_text(
            f"❌ الجلسة دي مش شغالة أو منتهية: {type(e).__name__}\n"
            "ابعت جلسة صحيحة تاني، أو دوس إلغاء."
        )

    try:
        await tmp.disconnect()
    except Exception:
        pass

    logger.info(
        f"Assistant {state['num']} session pasted by user {m.from_user.id}, "
        f"validated as account {getattr(me, 'id', '?')}."
    )
    await _finalize_pasted(m.from_user.id, session_string)


@app.on_message(_pending_stage_filter("phone"))
async def _sess_receive_phone(_, m: types.Message):
    if not m.text:
        return await m.reply_text("⚠️ ابعت رقم الهاتف كنص.")
    state = pending[m.from_user.id]
    phone = m.text.strip().replace(" ", "")
    try:
        await m.delete()
    except Exception:
        pass

    if not phone.startswith("+") or not phone[1:].isdigit():
        return await m.reply_text(
            "⚠️ اكتب الرقم بالصيغة الدولية وقدامه +، مثلا <code>+201234567890</code>"
        )

    tmp = Client(
        name=f"sess_login_{m.from_user.id}",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        in_memory=True,
    )
    await tmp.connect()

    try:
        sent = await tmp.send_code(phone)
    except errors.FloodWait as e:
        await tmp.disconnect()
        pending.pop(m.from_user.id, None)
        return await state["panel_msg"].edit_text(
            f"⚠️ استنى شوية قبل ما تحاول تاني (Flood wait: {e.value} ثانية)."
        )
    except Exception as e:
        await tmp.disconnect()
        pending.pop(m.from_user.id, None)
        return await state["panel_msg"].edit_text(f"❌ الرقم رفض: {type(e).__name__}")

    state.update(
        client=tmp,
        phone=phone,
        phone_code_hash=sent.phone_code_hash,
        stage="code",
    )
    await state["panel_msg"].edit_text(
        "📩 اتبعتلك كود على تيليجرام (أو SMS). ابعتهولي هنا."
    )


@app.on_message(_pending_stage_filter("code"))
async def _sess_receive_code(_, m: types.Message):
    if not m.text:
        return await m.reply_text("⚠️ ابعت الكود كنص.")
    state = pending[m.from_user.id]
    code = m.text.strip()
    try:
        await m.delete()
    except Exception:
        pass

    tmp: Client = state["client"]
    try:
        await tmp.sign_in(state["phone"], state["phone_code_hash"], code)
    except errors.SessionPasswordNeeded:
        state["stage"] = "password"
        return await state["panel_msg"].edit_text(
            "🔐 الحساب ده عليه مصادقة ثنائية. ابعتلي الباسورد بتاعها."
        )
    except errors.PhoneCodeInvalid:
        return await m.reply_text("⚠️ الكود غلط، ابعت الكود الصح تاني.")
    except errors.PhoneCodeExpired:
        await _cleanup(m.from_user.id)
        return await state["panel_msg"].edit_text(
            "⚠️ الكود انتهت صلاحيته. دوس \"تحديث\" وابدأ تاني."
        )
    except Exception as e:
        await _cleanup(m.from_user.id)
        return await state["panel_msg"].edit_text(f"❌ فشل تسجيل الدخول: {type(e).__name__}")

    await _finalize(m.from_user.id)


@app.on_message(_pending_stage_filter("password"))
async def _sess_receive_password(_, m: types.Message):
    if not m.text:
        return await m.reply_text("⚠️ ابعت الباسورد كنص.")
    state = pending[m.from_user.id]
    password = m.text.strip()
    try:
        await m.delete()
    except Exception:
        pass

    tmp: Client = state["client"]
    try:
        await tmp.check_password(password)
    except errors.PasswordHashInvalid:
        return await m.reply_text("⚠️ الباسورد غلط، جرب تاني.")
    except Exception as e:
        await _cleanup(m.from_user.id)
        return await state["panel_msg"].edit_text(f"❌ فشل التحقق من الباسورد: {type(e).__name__}")

    await _finalize(m.from_user.id)


# ------------------------------------------------------------------------------
# "👥 صلاحيات التحديث" - owner picks which admins see the button
# ------------------------------------------------------------------------------

async def _render_admins(target, page: int) -> None:
    sudoers = list(app.sudoers)
    allowed = set(await db.get_session_admins())
    others = [u for u in sudoers if u != app.owner]
    start = page * PER_PAGE
    chunk = others[start:start + PER_PAGE]
    has_next = len(others) > start + PER_PAGE

    text = (
        "<u><b>👥 صلاحيات تحديث الجلسات</b></u>\n\n"
        "اختار مين من المشرفين يقدر يشوف زر \"🔄 تحديث الجلسات\" ويستخدمه.\n"
        "(لازم يبقى مضاف كـ Admin/Sudo الأول)."
        if others else
        "<u><b>👥 صلاحيات تحديث الجلسات</b></u>\n\nمفيش مشرفين تانيين غيرك دلوقتي."
    )
    await _edit(target, text, buttons.sess_admins_markup(chunk, allowed, app.owner, page, has_next))


@app.on_callback_query(filters.regex(r"^sess_admins_\d+$"))
async def _sess_admins(_, query: types.CallbackQuery):
    if query.from_user.id != app.owner:
        return await _deny(query)
    await query.answer()
    page = int(query.data.rsplit("_", 1)[-1])
    await _render_admins(query.message, page)


@app.on_callback_query(filters.regex(r"^sess_admin_tgl_\d+_\d+$"))
async def _sess_admin_toggle(_, query: types.CallbackQuery):
    if query.from_user.id != app.owner:
        return await _deny(query)

    _, _, _, uid, page = query.data.split("_")
    uid, page = int(uid), int(page)

    allowed = set(await db.get_session_admins())
    if uid in allowed:
        await db.del_session_admin(uid)
        await query.answer("◻️ اتشال، الزر مش هيظهرله تاني.")
    else:
        await db.add_session_admin(uid)
        await query.answer("✅ اتضاف، الزر هيظهرله دلوقتي.")

    await _render_admins(query.message, page)
