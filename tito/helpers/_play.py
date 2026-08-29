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
# ==============================================================================

import asyncio

from pyrogram import enums, errors, types

from tito import app, config, db, queue, yt


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

            client = await db.get_client(m.chat.id)

            if not client:
                await safe_reply(
                    "⚠️ No assistant account is available for this chat."
                )
                return

            # --------------------------------------------------------------
            # First try to check assistant membership.
            #
            # IMPORTANT:
            # We use the bot to check the assistant, but if the bot doesn't
            # know the assistant peer yet, get_users() refreshes the peer.
            # --------------------------------------------------------------

            member = None

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
                    member = None

                except errors.ChatAdminRequired:
                    await safe_reply(
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
                    return

                except Exception:
                    member = None

            except errors.UserNotParticipant:
                member = None

            except errors.ChatAdminRequired:
                await safe_reply(
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
                return

            # --------------------------------------------------------------
            # Assistant is banned/restricted
            # --------------------------------------------------------------

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

            # --------------------------------------------------------------
            # Assistant is not in the group.
            # Join it using the assistant client.
            # --------------------------------------------------------------

            if member is None:

                invite_link = None

                # ----------------------------------------------------------
                # Public supergroup
                # ----------------------------------------------------------

                if m.chat.username:
                    invite_link = f"https://t.me/{m.chat.username}"

                # ----------------------------------------------------------
                # Private supergroup
                # ----------------------------------------------------------

                else:
                    try:
                        chat = await app.get_chat(m.chat.id)

                        invite_link = chat.invite_link

                        if not invite_link:
                            invite_link = await app.export_chat_invite_link(
                                m.chat.id
                            )

                    except errors.ChatAdminRequired:
                        await safe_reply(
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
                        return

                    except Exception as ex:
                        await safe_reply(
                            m.lang["play_invite_error"].format(
                                type(ex).__name__
                            )
                        )
                        return

                # ----------------------------------------------------------
                # Tell user assistant is joining
                # ----------------------------------------------------------

                umm = await safe_reply(
                    m.lang["play_invite"].format(app.name)
                )

                if umm:
                    await asyncio.sleep(2)

                # ----------------------------------------------------------
                # Join using the USERBOT / ASSISTANT
                # ----------------------------------------------------------

                try:
                    await client.join_chat(invite_link)

                except errors.UserAlreadyParticipant:
                    pass

                except errors.InviteRequestSent:

                    # Bot must approve the assistant's join request.
                    try:
                        await app.approve_chat_join_request(
                            m.chat.id,
                            client.id,
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
                # IMPORTANT:
                # Resolve the GROUP from the USERBOT.
                #
                # This makes sure the assistant itself knows the group
                # before PyTgCalls tries to use it.
                # ----------------------------------------------------------

                try:
                    await client.resolve_peer(m.chat.id)
                except Exception:
                    try:
                        await client.get_chat(m.chat.id)
                    except Exception as ex:
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

            try:
                await client.resolve_peer(m.chat.id)
            except Exception:
                try:
                    await client.get_chat(m.chat.id)
                except Exception as ex:
                    await safe_reply(
                        "⚠️ The assistant account cannot access this group.\n\n"
                        f"<code>{type(ex).__name__}</code>"
                    )
                    return

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