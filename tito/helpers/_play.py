# ==============================================================================
# _play.py - Play Command Validator
# ==============================================================================
# Validates everything before playing a song:
# - Chat type
# - User permissions
# - Queue limit
# - Play mode
# - Assistant availability
# - Assistant membership
# - Assistant join
#
# FIXES IN THIS VERSION:
# 1. Assistant membership is now checked FROM THE ASSISTANT'S OWN SIDE
#    (client.get_chat_member(chat, "me")). The old check used the BOT to
#    look up the assistant's user id; the bot usually can't resolve that
#    user (PeerIdInvalid), which was swallowed as "member = None", so the
#    code thought the assistant was missing and tried to invite it on
#    EVERY /play until Telegram answered with FloodWait.
# 2. FloodWait is now handled explicitly everywhere the assistant is
#    invited: the wait time is remembered per chat and no further invite
#    attempts are made until it expires. Playback continues instead of
#    being aborted (the assistant is very likely already in the chat).
# 3. A positive membership result is cached for a few minutes so the
#    checks/invites are not repeated on every command.
# 4. If the assistant can't resolve the group, its peer cache is refreshed
#    (get_dialogs) and retried once before giving up.
# 5. Real errors are logged instead of silently swallowed.
# ==============================================================================

import asyncio
import logging
import time

from pyrogram import enums, errors, types

from tito import app, config, db, queue, yt

logger = logging.getLogger(__name__)


# How long (seconds) a successful "assistant is in this chat" check is trusted.
ASSISTANT_VERIFY_TTL = 300

# (chat_id, assistant_id) -> timestamp of last successful verification
_assistant_verified: dict = {}

# chat_id -> unix time until which we must NOT try to invite the assistant
_join_flood_until: dict = {}


_BOT_ADMIN_MSG = (
    "<blockquote><b>🔐 Bot Admin Required</b></blockquote>\n\n"
    "<blockquote>"
    "To play music in this chat, I need to be an "
    "<b>administrator</b>.\n\n"
    "<b>Required permissions:</b>\n"
    "• Manage Voice Chats\n"
    "• Invite Users via Link\n"
    "• Delete Messages\n\n"
    "Please promote me as admin with the required permissions."
    "</blockquote>"
)


def _fmt_wait(seconds) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)

    parts = []

    if hours:
        parts.append(f"{hours} ساعة")

    if minutes:
        parts.append(f"{minutes} دقيقة")

    if secs or not parts:
        parts.append(f"{secs} ثانية")

    return " و ".join(parts)


async def _refresh_peers(client, limit: int = 200) -> None:
    """
    Make the assistant re-download its dialogs so its local peer cache
    (chat id -> access hash) knows about the group. Best effort only.
    """
    try:
        count = 0
        async for _ in client.get_dialogs(limit=limit):
            count += 1
        logger.info(
            f"Assistant peer cache refreshed ({count} dialogs)."
        )
    except Exception as e:
        logger.warning(
            f"Could not refresh assistant dialogs: "
            f"{type(e).__name__}: {e}"
        )


async def _assistant_membership(client, chat_id: int):
    """
    Ask the ASSISTANT itself whether it is in the chat.

    Returns the ChatMember object if it can see itself in the chat,
    otherwise None (not a member / unknown).
    """
    for attempt in range(2):
        try:
            return await client.get_chat_member(chat_id, "me")

        except errors.UserNotParticipant:
            logger.info(
                f"Assistant-side check: not a participant of {chat_id}"
            )
            return None

        except (
            errors.PeerIdInvalid,
            errors.ChannelInvalid,
            KeyError,
        ) as e:
            if attempt == 0:
                logger.info(
                    f"Assistant doesn't know chat {chat_id} yet "
                    f"({type(e).__name__}); refreshing its peers"
                )
                await _refresh_peers(client)
                continue

            logger.warning(
                f"Assistant-side check failed for {chat_id}: "
                f"{type(e).__name__}: {e}"
            )
            return None

        except errors.FloodWait as e:
            logger.warning(
                f"FloodWait ({e.value}s) during assistant-side "
                f"membership check for {chat_id}"
            )
            return None

        except Exception as e:
            logger.warning(
                f"Assistant-side membership check error for {chat_id}: "
                f"{type(e).__name__}: {e}"
            )
            return None

    return None


async def _assistant_can_resolve(client, chat_id: int):
    """
    Make sure the assistant can resolve the group. If not, refresh its
    peer cache once and retry.

    Returns (ok: bool, last_exception | None).
    """
    last_exc = None

    for attempt in range(2):
        try:
            await client.resolve_peer(chat_id)
            return True, None

        except Exception:
            try:
                await client.get_chat(chat_id)
                return True, None

            except Exception as ex:
                last_exc = ex

                if attempt == 0:
                    await _refresh_peers(client)

    return False, last_exc


def checkUB(play):
    async def wrapper(_, m: types.Message):

        async def safe_reply(text):
            try:
                return await m.reply_text(text)
            except (
                errors.ChatWriteForbidden,
                errors.ChatSendPlainForbidden,
            ):
                return None
            except Exception:
                return None

        # ----------------------------------------------------------------------
        # Validate user
        # ----------------------------------------------------------------------

        is_channel_post = (
            m.sender_chat is not None
            and m.sender_chat.id == m.chat.id
            and m.chat.type == enums.ChatType.CHANNEL
        )

        if not m.from_user and not is_channel_post:
            await safe_reply(m.lang["play_user_invalid"])
            return

        # ----------------------------------------------------------------------
        # Validate chat type
        # ----------------------------------------------------------------------

        if m.chat.type not in (enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL, enums.ChatType.GROUP):
            await safe_reply(m.lang["play_chat_invalid"])

            try:
                await app.leave_chat(m.chat.id)
            except Exception:
                pass

            return

        # ----------------------------------------------------------------------
        # Validate command
        # ----------------------------------------------------------------------

        if not m.reply_to_message and (
            len(m.command) < 2
            or (len(m.command) == 2 and m.command[1] == "-f")
        ):
            await safe_reply(m.lang["play_usage"])
            return

        # ----------------------------------------------------------------------
        # Queue limit
        # ----------------------------------------------------------------------

        if len(queue.get_queue(m.chat.id)) >= config.QUEUE_LIMIT:
            await safe_reply(
                m.lang["play_queue_full"].format(config.QUEUE_LIMIT)
            )
            return

        # ----------------------------------------------------------------------
        # Command / force / video
        # ----------------------------------------------------------------------

        command = m.command[0].lower()

        force = command in ("شغل_فرض", "شغل_فيديو_فرض", "fplay", "vfplay") or (
            len(m.command) > 1 and "-f" in m.command[1]
        )

        video_requested = command in ("شغل_فيديو", "شغل_فيديو_فرض", "فيديو", "فيد", "vplay", "vfplay", "video", "vid")

        if video_requested and not await db.get_vplay_enabled():
            await safe_reply(m.lang["play_video_disabled"])
            return

        video = video_requested

        # ----------------------------------------------------------------------
        # URL
        # ----------------------------------------------------------------------

        url = yt.url(m)

        if url and not m.reply_to_message and not yt.valid(url):
            await safe_reply(m.lang["play_unsupported"])
            return

        # ----------------------------------------------------------------------
        # Play mode / permissions
        # ----------------------------------------------------------------------

        play_mode = await db.get_play_mode(m.chat.id)

        if (play_mode or force) and not is_channel_post:
            adminlist = await db.get_admins(m.chat.id)

            if (
                m.from_user.id not in adminlist
                and not await db.is_auth(m.chat.id, m.from_user.id)
                and m.from_user.id not in app.sudoers
            ):
                await safe_reply(m.lang["play_admin"])
                return

        # ----------------------------------------------------------------------
        # Make sure assistant is available in the group
        # ----------------------------------------------------------------------

        if m.chat.id not in db.active_calls:

            # ------------------------------------------------------------------
            # Require the BOT itself to already be admin before the assistant
            # is allowed to join at all. This must run first: previously a
            # public group's invite link (t.me/username) let the assistant
            # join even while the bot wasn't admin yet, since joining a
            # public chat doesn't need admin rights. Checking explicitly here
            # closes that gap for every chat type, not just the cases where
            # Telegram happens to raise ChatAdminRequired on some other call.
            # ------------------------------------------------------------------

            try:
                bot_member = await app.get_chat_member(m.chat.id, "me")
            except Exception:
                bot_member = None

            if (
                bot_member is None
                or bot_member.status
                not in (
                    enums.ChatMemberStatus.ADMINISTRATOR,
                    enums.ChatMemberStatus.OWNER,
                )
            ):
                await safe_reply(
                    "<blockquote><b>🔐 Bot Admin Required</b></blockquote>\n\n"
                    "<blockquote>"
                    "Please promote me as an <b>administrator</b> first — "
                    "the assistant account won't join this chat until then.\n\n"
                    "<b>Required permissions:</b>\n"
                    "• Manage Voice Chats\n"
                    "• Invite Users via Link\n"
                    "• Delete Messages"
                    "</blockquote>"
                )
                return

            client = await db.get_client(m.chat.id)

            if not client:
                await safe_reply(
                    "⚠️ No assistant account is available for this chat."
                )
                return

            member = None
            assistant_ok = False
            joined_ok = False

            cache_key = (m.chat.id, client.id)

            # --------------------------------------------------------------
            # 0) Recently verified? Skip all membership work.
            # --------------------------------------------------------------

            verified_at = _assistant_verified.get(cache_key)

            if (
                verified_at is not None
                and time.time() - verified_at < ASSISTANT_VERIFY_TTL
            ):
                assistant_ok = True

            # --------------------------------------------------------------
            # 1) Ask the ASSISTANT itself (most reliable).
            #
            # The bot often can't resolve the assistant's user id, which
            # made the old bot-side check return "not a member" even when
            # the assistant was already in the group.
            # --------------------------------------------------------------

            if not assistant_ok:

                assistant_member = await _assistant_membership(
                    client,
                    m.chat.id,
                )

                if assistant_member is not None:

                    logger.info(
                        f"Assistant {client.id} status in {m.chat.id}: "
                        f"{assistant_member.status}"
                    )

                    if assistant_member.status in (
                        enums.ChatMemberStatus.BANNED,
                        enums.ChatMemberStatus.RESTRICTED,
                    ):
                        # Handled by the unban logic below.
                        member = assistant_member

                    elif (
                        assistant_member.status
                        != enums.ChatMemberStatus.LEFT
                    ):
                        assistant_ok = True

            # --------------------------------------------------------------
            # 2) Assistant not confirmed yet -> fall back to the bot-side
            #    check, ban handling and, only if really needed, the invite.
            # --------------------------------------------------------------

            if not assistant_ok:

                # ----------------------------------------------------------
                # Bot-side membership check.
                #
                # IMPORTANT:
                # We use the bot to check the assistant, but if the bot
                # doesn't know the assistant peer yet, get_users() refreshes
                # the peer.
                # ----------------------------------------------------------

                if member is None:

                    try:
                        member = await app.get_chat_member(
                            m.chat.id,
                            client.id,
                        )

                    except errors.ChannelInvalid:
                        try:
                            # The bot doesn't have this chat's peer cached yet.
                            # Force a refresh by fetching the chat directly.
                            await app.get_chat(m.chat.id)

                            member = await app.get_chat_member(
                                m.chat.id,
                                client.id,
                            )

                        except errors.UserNotParticipant:
                            member = None

                        except Exception:
                            await safe_reply(
                                "⚠️ <b>تعذر التعرف على المجموعة.</b>\n"
                                "جرّب تشيل البوت وتضيفه تاني للمجموعة، "
                                "أو ابعت أي رسالة عادية فيها الأول ثم اعد المحاولة."
                            )
                            return

                    except errors.PeerIdInvalid:
                        try:
                            # Refresh the bot's peer information.
                            assistant = await app.get_users(client.id)

                            member = await app.get_chat_member(
                                m.chat.id,
                                assistant.id,
                            )

                        except errors.UserNotParticipant:
                            member = None

                        except errors.PeerIdInvalid:
                            logger.warning(
                                f"Bot can't resolve assistant {client.id} "
                                f"(PeerIdInvalid); treating as unknown"
                            )
                            member = None

                        except errors.ChatAdminRequired:
                            await safe_reply(_BOT_ADMIN_MSG)
                            return

                        except Exception as ex:
                            logger.warning(
                                f"Bot-side assistant lookup failed: "
                                f"{type(ex).__name__}: {ex}"
                            )
                            member = None

                    except errors.UserNotParticipant:
                        member = None

                    except errors.ChatAdminRequired:
                        await safe_reply(_BOT_ADMIN_MSG)
                        return

                # ----------------------------------------------------------
                # Assistant is banned/restricted
                # ----------------------------------------------------------

                if member and member.status in [
                    enums.ChatMemberStatus.BANNED,
                    enums.ChatMemberStatus.RESTRICTED,
                ]:
                    try:
                        await app.unban_chat_member(
                            chat_id=m.chat.id,
                            user_id=client.id,
                        )

                        # Refresh membership after unban.
                        try:
                            member = await app.get_chat_member(
                                m.chat.id,
                                client.id,
                            )
                        except Exception:
                            member = None

                    except Exception:
                        await safe_reply(
                            m.lang["play_banned"].format(
                                app.name,
                                client.id,
                                client.mention,
                                (
                                    f"@{client.username}"
                                    if client.username
                                    else None
                                ),
                            )
                        )
                        return

                # ----------------------------------------------------------
                # Assistant is not in the group (as far as we can tell).
                # Join it using the assistant client.
                # ----------------------------------------------------------

                if member is None:

                    invite_link = None
                    flood_notice = None

                    flood_left = (
                        _join_flood_until.get(m.chat.id, 0)
                        - time.time()
                    )

                    umm = None

                    if flood_left > 0:

                        # We are still inside a Telegram FloodWait window
                        # for inviting the assistant. Do NOT hit the API
                        # again (that only extends the wait). Carry on:
                        # the assistant is most likely already in the chat.

                        logger.warning(
                            f"Skipping assistant invite for {m.chat.id}: "
                            f"FloodWait active for another "
                            f"{int(flood_left)}s"
                        )

                        flood_notice = (
                            "<blockquote>"
                            "⏳ <b>تليجرام حاطط حد مؤقت على دعوة المساعد.</b>\n"
                            f"المتبقي: {_fmt_wait(flood_left)}\n"
                            "لو المساعد موجود في الجروب هيكمّل التشغيل عادي، "
                            "ولو مش موجود ضيفه يدويًا."
                            "</blockquote>"
                        )

                    else:

                        # ------------------------------------------------------
                        # Public supergroup
                        # ------------------------------------------------------

                        if m.chat.username:
                            invite_link = f"https://t.me/{m.chat.username}"

                        # ------------------------------------------------------
                        # Private supergroup
                        # ------------------------------------------------------

                        else:
                            try:
                                chat = await app.get_chat(m.chat.id)

                                invite_link = chat.invite_link

                                if not invite_link:
                                    invite_link = await app.export_chat_invite_link(
                                        m.chat.id
                                    )

                            except errors.ChatAdminRequired:
                                await safe_reply(_BOT_ADMIN_MSG)
                                return

                            except errors.FloodWait as fw:
                                _join_flood_until[m.chat.id] = (
                                    time.time() + fw.value
                                )
                                logger.warning(
                                    f"FloodWait {fw.value}s while getting "
                                    f"invite link for {m.chat.id}"
                                )
                                await safe_reply(
                                    "<blockquote>"
                                    "⏳ <b>تليجرام حاطط حد مؤقت.</b>\n"
                                    f"استنى {_fmt_wait(fw.value)} وجرّب تاني، "
                                    "أو ضيف المساعد للجروب يدويًا."
                                    "</blockquote>"
                                )
                                return

                            except Exception as ex:
                                await safe_reply(
                                    m.lang["play_invite_error"].format(
                                        type(ex).__name__
                                    )
                                )
                                return

                        # ------------------------------------------------------
                        # Tell user assistant is joining
                        # ------------------------------------------------------

                        umm = await safe_reply(
                            m.lang["play_invite"].format(app.name)
                        )

                        if umm:
                            await asyncio.sleep(2)

                        # ------------------------------------------------------
                        # Join using the USERBOT / ASSISTANT
                        # ------------------------------------------------------

                        try:
                            await client.join_chat(invite_link)
                            joined_ok = True

                        except errors.UserAlreadyParticipant:
                            joined_ok = True

                        except errors.FloodWait as fw:

                            # Remember it so we don't retry until it expires.
                            _join_flood_until[m.chat.id] = (
                                time.time() + fw.value
                            )

                            logger.warning(
                                f"FloodWait {fw.value}s while inviting "
                                f"assistant {client.id} to {m.chat.id}"
                            )

                            flood_notice = (
                                "<blockquote>"
                                "⏳ <b>تليجرام حاطط حد مؤقت على دعوة المساعد.</b>\n"
                                f"المدة: {_fmt_wait(fw.value)}\n"
                                "لو المساعد موجود في الجروب هيكمّل التشغيل عادي، "
                                "ولو مش موجود ضيفه يدويًا."
                                "</blockquote>"
                            )

                        except errors.InviteRequestSent:

                            # Bot must approve the assistant's join request.
                            try:
                                await app.approve_chat_join_request(
                                    m.chat.id,
                                    client.id,
                                )
                                joined_ok = True

                            except errors.FloodWait as fw:
                                _join_flood_until[m.chat.id] = (
                                    time.time() + fw.value
                                )

                                logger.warning(
                                    f"FloodWait {fw.value}s while approving "
                                    f"assistant join request for {m.chat.id}"
                                )

                                flood_notice = (
                                    "<blockquote>"
                                    "⏳ <b>تليجرام حاطط حد مؤقت.</b>\n"
                                    f"المدة: {_fmt_wait(fw.value)}"
                                    "</blockquote>"
                                )

                            except errors.ChatAdminRequired:
                                if umm:
                                    try:
                                        await umm.edit_text(
                                            "<blockquote>"
                                            "<b>🔐 Bot Admin Required</b>"
                                            "</blockquote>\n\n"
                                            "<blockquote>"
                                            "The assistant requested to join, but the "
                                            "bot needs admin permissions to approve it."
                                            "</blockquote>"
                                        )
                                    except Exception:
                                        pass

                                return

                            except Exception as ex:
                                if umm:
                                    try:
                                        await umm.edit_text(
                                            m.lang["play_invite_error"].format(
                                                type(ex).__name__
                                            )
                                        )
                                    except Exception:
                                        pass

                                return

                        except errors.ChatAdminRequired:
                            if umm:
                                try:
                                    await umm.edit_text(
                                        "<blockquote>"
                                        "<b>🔐 Bot Admin Required</b>"
                                        "</blockquote>\n\n"
                                        "<blockquote>"
                                        "The bot needs administrator permissions "
                                        "to manage the assistant."
                                        "</blockquote>"
                                    )
                                except Exception:
                                    pass

                            return

                        except Exception as ex:
                            logger.warning(
                                f"Assistant join failed for {m.chat.id}: "
                                f"{type(ex).__name__}: {ex}"
                            )

                            if umm:
                                try:
                                    await umm.edit_text(
                                        m.lang["play_invite_error"].format(
                                            type(ex).__name__
                                        )
                                    )
                                except Exception:
                                    pass

                            return

                    # ----------------------------------------------------------
                    # Delete joining message
                    # ----------------------------------------------------------

                    if umm:
                        try:
                            await umm.delete()
                        except Exception:
                            pass

                    # ----------------------------------------------------------
                    # FloodWait notice (we keep going instead of aborting)
                    # ----------------------------------------------------------

                    if flood_notice:
                        await safe_reply(flood_notice)

                    # ----------------------------------------------------------
                    # IMPORTANT:
                    # Resolve the GROUP from the USERBOT.
                    #
                    # This makes sure the assistant itself knows the group
                    # before PyTgCalls tries to use it.
                    # ----------------------------------------------------------

                    resolved, ex = await _assistant_can_resolve(
                        client,
                        m.chat.id,
                    )

                    if not resolved:
                        await safe_reply(
                            "⚠️ Assistant could not access this group.\n\n"
                            f"<code>{type(ex).__name__}</code>"
                        )
                        return

            # ------------------------------------------------------------------
            # FINAL CHECK:
            # Make sure USERBOT can resolve the group.
            # This is important before starting PyTgCalls.
            # ------------------------------------------------------------------

            resolved, ex = await _assistant_can_resolve(
                client,
                m.chat.id,
            )

            if not resolved:
                await safe_reply(
                    "⚠️ The assistant account cannot access this group.\n\n"
                    f"<code>{type(ex).__name__}</code>"
                )
                return

            # Remember a positive result so the next commands skip all of it.
            if assistant_ok or joined_ok:
                _assistant_verified[cache_key] = time.time()

        # ----------------------------------------------------------------------
        # Delete command
        # ----------------------------------------------------------------------

        try:
            await m.delete()
        except Exception:
            pass

        # ----------------------------------------------------------------------
        # Start actual playback
        # ----------------------------------------------------------------------

        return await play(
            _,
            m,
            force,
            url,
            video,
        )

    return wrapper
