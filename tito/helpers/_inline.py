# ==============================================================================
# _inline.py - Keyboard Buttons
# ==============================================================================
# Helper methods to generate all the inline keyboards (play controls, help menus, etc).
# Buttons are colored using Telegram Bot API's native button "style" field
# (Bot API 9.4+, supported by kurigram via pyrogram.enums.ButtonStyle):
#   - DANGER  (red)   -> back / close / delete / stop actions
#   - SUCCESS (green) -> main positive actions (resume, add me, open link)
#   - PRIMARY (blue)  -> everything else
# ==============================================================================

import re

from pyrogram import types, enums

from tito import app, config, lang
from tito.core.emojis import CUSTOM_EMOJIS

# Telegram inline-keyboard button LABELS are still plain text: the Bot API
# does not let you embed message entities (including custom/premium emoji)
# inside the button's `text` field. Values coming out of the lang system
# already went through apply_custom_emojis() and may contain raw
# `<emoji id="...">🎵</emoji>` tags, which would otherwise show up as
# literal, broken text on a button. Strip them back down to the plain
# fallback emoji whenever we build a button.
_EMOJI_TAG_RE = re.compile(r'<emoji id="\d+">(.*?)</emoji>')

# What buttons DO support (Bot API 9.4+ / kurigram) is a separate
# `icon_custom_emoji_id` field: a small custom-emoji icon rendered on the
# button itself, alongside the plain-text label. If a button's label
# *starts* with an emoji that has a real custom-emoji id configured in
# tito/core/emojis.py, we automatically move that emoji into the icon slot
# and drop it from the visible text (so it isn't shown twice). Emojis with
# no id set ("") are left as plain text in the label, same as before.
_LEADING_EMOJI_RE = re.compile(
    "^(" + "|".join(
        re.escape(e) for e in sorted(CUSTOM_EMOJIS, key=len, reverse=True) if e
    ) + r")\s*"
)


def _plain_text(text):
    if isinstance(text, str) and "<emoji" in text:
        return _EMOJI_TAG_RE.sub(r"\1", text)
    return text


def _extract_icon_emoji(text):
    """If `text` starts with a known emoji that has a custom-emoji id
    configured, return (custom_emoji_id, remaining_text). Otherwise
    return (None, text) unchanged."""
    if not isinstance(text, str):
        return None, text
    match = _LEADING_EMOJI_RE.match(text)
    if not match:
        return None, text
    custom_id = CUSTOM_EMOJIS.get(match.group(1))
    if not custom_id:
        return None, text
    return custom_id, text[match.end():]


class Inline:
    def __init__(self):
        self.ikm = types.InlineKeyboardMarkup
        self._raw_ikb = types.InlineKeyboardButton

    def ikb(
        self,
        text,
        style: "enums.ButtonStyle" = enums.ButtonStyle.PRIMARY,
        icon_custom_emoji_id: str = None,
        **kwargs,
    ):
        """Build an InlineKeyboardButton with an explicit color style.

        Defaults to PRIMARY (blue). Pass style=enums.ButtonStyle.DANGER for
        back/close/cancel/stop buttons, or SUCCESS for the main positive
        action in a row.

        If `icon_custom_emoji_id` isn't passed explicitly, it's auto-filled
        from a leading emoji in `text` when that emoji has a custom id set
        in tito/core/emojis.py (CUSTOM_EMOJIS).
        """
        text = _plain_text(text)
        if icon_custom_emoji_id is None:
            icon_custom_emoji_id, text = _extract_icon_emoji(text)
        return self._raw_ikb(
            text=text,
            style=style,
            icon_custom_emoji_id=icon_custom_emoji_id,
            **kwargs,
        )

    def cancel_dl(self, text) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [[self.ikb(text=text, callback_data="cancel_dl", style=enums.ButtonStyle.DANGER)]]
        )

    def controls(
        self,
        chat_id: int,
        status: str = None,
        timer: str = None,
        remove: bool = False,
    ) -> types.InlineKeyboardMarkup:
        keyboard = []
        if status:
            keyboard.append(
                [self.ikb(
                    text=status, callback_data=f"controls status {chat_id}")]
            )
        elif timer:
            keyboard.append(
                [self.ikb(
                    text=timer, callback_data=f"controls status {chat_id}")]
            )

        if not remove:
            # Main control buttons row
            keyboard.append(
                [
                    self.ikb(
                        text="▷", callback_data=f"controls resume {chat_id}",
                        style=enums.ButtonStyle.SUCCESS),
                    self.ikb(
                        text="II", callback_data=f"controls pause {chat_id}"),
                    self.ikb(
                        text="↻", callback_data=f"controls replay {chat_id}"),
                    self.ikb(
                        text="‣‣I", callback_data=f"controls skip {chat_id}"),
                    self.ikb(
                        text="▢", callback_data=f"controls stop {chat_id}",
                        style=enums.ButtonStyle.DANGER),
                ]
            )
            # "Add me to your group" button as full-width button at bottom
            keyboard.append(
                [
                    self.ikb(
                        text="➕ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ",
                        url=f"https://t.me/{app.username}?startgroup=true",
                        style=enums.ButtonStyle.SUCCESS),
                ]
            )
        return self.ikm(keyboard)

    def help_markup(
        self, _lang: dict, back: bool = False
    ) -> types.InlineKeyboardMarkup:
        if back:
            rows = [
                [
                    self.ikb(text=_lang["back"], callback_data="help_main",
                             style=enums.ButtonStyle.DANGER),
                ]
            ]
        else:
            # Help menu with categorized buttons (3 per row)
            rows = [
                [
                    self.ikb(text=_lang["help_btn_admins"], callback_data="help_admins"),
                    self.ikb(text=_lang["help_btn_auth"], callback_data="help_auth"),
                    self.ikb(text=_lang["help_btn_broadcast"], callback_data="help_broadcast"),
                ],
                [
                    self.ikb(text=_lang["help_btn_loop"], callback_data="help_loop"),
                    self.ikb(text=_lang["help_btn_play"], callback_data="help_play",
                              style=enums.ButtonStyle.SUCCESS),
                    self.ikb(text=_lang["help_btn_queue"], callback_data="help_queue"),
                ],
                [
                    self.ikb(text=_lang["help_btn_blchat"], callback_data="help_blchat"),
                    self.ikb(text=_lang["help_btn_bluser"], callback_data="help_bluser"),
                    self.ikb(text=_lang["help_btn_seek"], callback_data="help_seek"),
                ],
                [
                    self.ikb(text=_lang["help_btn_games"], callback_data="help_games"),
                    self.ikb(text=_lang["help_btn_ping"], callback_data="help_ping"),
                    self.ikb(text=_lang["help_btn_stats"], callback_data="help_stats"),
                ],
                [
                    self.ikb(text=_lang["help_btn_sudo"], callback_data="help_sudo"),
                    self.ikb(text=_lang["help_btn_lang"], callback_data="help_lang"),
                ],
                [
                    self.ikb(
                        text=_lang["help_btn_choose_lang"],
                        callback_data="lang_menu",
                        style=enums.ButtonStyle.SUCCESS,
                    ),
                ],
                [
                    self.ikb(text=_lang["back"], callback_data="start",
                             style=enums.ButtonStyle.DANGER),
                ]
            ]
        return self.ikm(rows)

    def lang_markup(self, _lang: dict = None, back_target: str = "start") -> types.InlineKeyboardMarkup:
        """Build the language-selection keyboard (2 per row), with a back button."""
        from tito.core.lang import lang_codes

        rows = []
        row = []
        for code, name in lang_codes.items():
            row.append(self.ikb(text=name, callback_data=f"set_lang_{code}_{back_target}"))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        back_text = _lang["back"] if _lang else "ʙᴀᴄᴋ"
        rows.append(
            [self.ikb(text=back_text, callback_data=back_target, style=enums.ButtonStyle.DANGER)]
        )
        return self.ikm(rows)


    def ping_markup(self, text: str) -> types.InlineKeyboardMarkup:
        return self.ikm([
            [
                self.ikb(text="📢 ᴄʜᴀɴɴᴇʟ", url=config.SUPPORT_CHANNEL),
                self.ikb(text="🆘 ꜱᴜᴘᴘᴏʀᴛ", url=config.SUPPORT_CHAT),
            ],
            [
                self.ikb(text="➕ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ", url=f"https://t.me/{app.username}?startgroup=true",
                          style=enums.ButtonStyle.SUCCESS),
            ]
        ])

    def play_queued(
        self, chat_id: int, item_id: str, _text: str
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(
                        text="▷", callback_data=f"controls resume {chat_id}",
                        style=enums.ButtonStyle.SUCCESS),
                    self.ikb(
                        text="∣ ∣", callback_data=f"controls pause {chat_id}"),
                    self.ikb(
                        text=">>", callback_data=f"controls skip {chat_id}"),
                    self.ikb(
                        text="▣", callback_data=f"controls stop {chat_id}",
                        style=enums.ButtonStyle.DANGER),
                ],
                [
                    self.ikb(
                        text="➕ ᴀᴅᴅ ᴍᴇ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ",
                        url=f"https://t.me/{app.username}?startgroup=true",
                        style=enums.ButtonStyle.SUCCESS),
                ]
            ]
        )

    def queue_markup(
        self, chat_id: int, _text: str, playing: bool
    ) -> types.InlineKeyboardMarkup:
        _action = "pause" if playing else "resume"
        _style = enums.ButtonStyle.PRIMARY if playing else enums.ButtonStyle.SUCCESS
        return self.ikm(
            [[self.ikb(
                text=_text, callback_data=f"controls {_action} {chat_id} q",
                style=_style)]]
        )

    def settings_markup(
        self, lang: dict, admin_only: bool, language: str, chat_id: int
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(
                        text=lang["play_mode"] + " ➜",
                        callback_data=f"controls status {chat_id}",
                    ),
                    self.ikb(text=admin_only, callback_data="playmode"),
                ],
            ]
        )

    def start_key(
        self, lang: dict, private: bool = False, is_owner: bool = False
    ) -> types.InlineKeyboardMarkup:
        rows = [
            [
                self.ikb(
                    text=lang["add_me"],
                    url=f"https://t.me/{app.username}?startgroup=true",
                    style=enums.ButtonStyle.SUCCESS,
                )
            ],
            [
                self.ikb(text=lang["help"], callback_data="help"),
                self.ikb(text=lang["language"], callback_data="lang_menu"),
            ],
            [
                self.ikb(text=lang["support"], url=config.SUPPORT_CHAT),
                self.ikb(text=lang["channel"], url=config.SUPPORT_CHANNEL),
            ],
        ]
        if private and is_owner:
            rows.append(
                [
                    self.ikb(
                        text="👑 Admins",
                        callback_data="sudo_panel",
                    ),
                    self.ikb(
                        text="🎛️ لوحة التحكم",
                        callback_data="owner_panel",
                    ),
                ]
            )
        if private:
            rows += [
                [
                    self.ikb(
                        text=lang["source"],
                        url="https://tito-pied.vercel.app/",
                    )
                ]
            ]
        return self.ikm(rows)

    def sudo_panel_markup(self, sudoers: list, owner_id: int) -> types.InlineKeyboardMarkup:
        """Owner-only panel: list current sudo admins with remove buttons,
        plus an add button and a back button."""
        rows = []
        for user_id in sudoers:
            if user_id == owner_id:
                continue
            rows.append(
                [
                    self.ikb(
                        text=f"➖ {user_id}",
                        callback_data=f"sudo_rm_{user_id}",
                        style=enums.ButtonStyle.DANGER,
                    )
                ]
            )
        rows.append(
            [
                self.ikb(
                    text="➕ إضافة مشرف",
                    callback_data="sudo_add",
                    style=enums.ButtonStyle.SUCCESS,
                )
            ]
        )
        rows.append(
            [
                self.ikb(text="🔙 رجوع", callback_data="start", style=enums.ButtonStyle.DANGER),
            ]
        )
        return self.ikm(rows)

    # ==========================================================================
    # OWNER CONTROL PANEL - button-based users/groups/channels/broadcast
    # management, opened from the "🎛️ لوحة التحكم" button on /start.
    # ==========================================================================

    def op_main_markup(self, fsub_enabled: bool = False) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(text="👤 المستخدمين", callback_data="op_users_0"),
                    self.ikb(text="👥 الجروبات", callback_data="op_grp_0"),
                ],
                [
                    self.ikb(text="📢 القنوات", callback_data="op_chn_0"),
                    self.ikb(text="📣 الإذاعة", callback_data="op_bc"),
                ],
                [
                    self.ikb(
                        text=(
                            "🔔 الاشتراك الإجباري: مفعّل ✅"
                            if fsub_enabled
                            else "🔕 الاشتراك الإجباري: معطّل ◻️"
                        ),
                        callback_data="op_fsub_toggle",
                        style=enums.ButtonStyle.SUCCESS if fsub_enabled else enums.ButtonStyle.DANGER,
                    ),
                ],
                [
                    self.ikb(text="🔙 رجوع", callback_data="start",
                              style=enums.ButtonStyle.DANGER),
                ],
            ]
        )

    def op_users_markup(
        self, rows_data: list[tuple[int, str, str, bool]], page: int, has_next: bool
    ) -> types.InlineKeyboardMarkup:
        """rows_data: list of (user_id, label, chat_url, is_blocked) for the
        current page. `label` is plain text (name + @username or ID) and is
        rendered as a URL button that opens a chat with that person directly
        when tapped; the second button toggles their block status."""
        rows = []
        for uid, label, chat_url, blocked in rows_data:
            rows.append(
                [
                    self.ikb(text=label, url=chat_url),
                    self.ikb(
                        text="✅ فك الحظر" if blocked else "🚫 حظر",
                        callback_data=f"op_utgl_{uid}_{page}",
                        style=enums.ButtonStyle.SUCCESS if blocked else enums.ButtonStyle.DANGER,
                    ),
                ]
            )
        nav = []
        if page > 0:
            nav.append(self.ikb(text="◀️", callback_data=f"op_users_{page - 1}"))
        if has_next:
            nav.append(self.ikb(text="▶️", callback_data=f"op_users_{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append(
            [self.ikb(text="🔙 رجوع", callback_data="owner_panel",
                       style=enums.ButtonStyle.DANGER)]
        )
        return self.ikm(rows)

    def op_chats_markup(
        self,
        rows_data: list[tuple[int, str]],
        kind: str,
        page: int,
        has_next: bool,
    ) -> types.InlineKeyboardMarkup:
        """rows_data: list of (chat_id, label) for the current page.
        kind: 'g' for groups or 'c' for channels (kept short for callback_data)."""
        rows = []
        for cid, label in rows_data:
            rows.append(
                [self.ikb(text=label, callback_data=f"op_cd_{kind}_{cid}_{page}")]
            )
        nav = []
        list_cb = "op_grp" if kind == "g" else "op_chn"
        if page > 0:
            nav.append(self.ikb(text="◀️", callback_data=f"{list_cb}_{page - 1}"))
        if has_next:
            nav.append(self.ikb(text="▶️", callback_data=f"{list_cb}_{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append(
            [self.ikb(text="🔙 رجوع", callback_data="owner_panel",
                       style=enums.ButtonStyle.DANGER)]
        )
        return self.ikm(rows)

    def op_chat_detail_markup(
        self, kind: str, chat_id: int, page: int, blocked: bool
    ) -> types.InlineKeyboardMarkup:
        list_cb = "op_grp" if kind == "g" else "op_chn"
        return self.ikm(
            [
                [
                    self.ikb(
                        text="✅ فك حظر الشات" if blocked else "🚫 حظر الشات (تعطيل البوت فيه)",
                        callback_data=f"op_cblk_{kind}_{chat_id}_{page}",
                        style=enums.ButtonStyle.SUCCESS if blocked else enums.ButtonStyle.DANGER,
                    )
                ],
                [
                    self.ikb(
                        text="🚪 خلي البوت يغادر",
                        callback_data=f"op_clv_{kind}_{chat_id}_{page}",
                        style=enums.ButtonStyle.DANGER,
                    )
                ],
                [
                    self.ikb(text="🔙 رجوع للقائمة", callback_data=f"{list_cb}_{page}",
                              style=enums.ButtonStyle.DANGER),
                ],
            ]
        )

    def op_leave_confirm_markup(
        self, kind: str, chat_id: int, page: int
    ) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(text="✅ اه اكيد", callback_data=f"op_clvy_{kind}_{chat_id}_{page}",
                              style=enums.ButtonStyle.SUCCESS),
                    self.ikb(text="❌ لا", callback_data=f"op_cd_{kind}_{chat_id}_{page}",
                              style=enums.ButtonStyle.DANGER),
                ],
            ]
        )

    def op_broadcast_cancel_markup(self) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [[self.ikb(text="❌ إلغاء", callback_data="op_bc_abort",
                        style=enums.ButtonStyle.DANGER)]]
        )

    def op_broadcast_targets_markup(
        self, groups: bool, channels: bool, users: bool, pin: bool
    ) -> types.InlineKeyboardMarkup:
        def mark(flag):
            return "✅" if flag else "◻️"

        return self.ikm(
            [
                [
                    self.ikb(text=f"{mark(groups)} الجروبات", callback_data="op_bc_tgl_g"),
                    self.ikb(text=f"{mark(channels)} القنوات", callback_data="op_bc_tgl_c"),
                ],
                [
                    self.ikb(text=f"{mark(users)} المستخدمين", callback_data="op_bc_tgl_u"),
                    self.ikb(text=f"{mark(pin)} تثبيت الرسالة", callback_data="op_bc_tgl_p"),
                ],
                [
                    self.ikb(text="🚀 ابعت دلوقتي", callback_data="op_bc_send",
                              style=enums.ButtonStyle.SUCCESS),
                    self.ikb(text="❌ إلغاء", callback_data="op_bc_abort",
                              style=enums.ButtonStyle.DANGER),
                ],
            ]
        )

    def yt_key(self, link: str) -> types.InlineKeyboardMarkup:
        return self.ikm(
            [
                [
                    self.ikb(text="ᴄᴏᴘʏ ʟɪɴᴋ", copy_text=link),
                    self.ikb(text="ᴏᴘᴇɴ ɪɴ ʏᴏᴜᴛᴜʙᴇ", url=link,
                              style=enums.ButtonStyle.SUCCESS),
                ],
            ]
        )
