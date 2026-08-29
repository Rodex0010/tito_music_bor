# ==============================================================================
# keywords.py - Fun Keyword Auto-Replies
# ==============================================================================
# Little easter eggs that make the bot feel alive:
#   - Someone writes "بوت"    -> a creative, self-aware reply
#   - Someone writes "كريزه"  -> a cute/sweet reply
# Pure text triggers, no command prefix needed. Blacklisted users and other
# bots are ignored, same as the rest of the plugins.
# ==============================================================================

import random

from pyrogram import enums, filters, types

from tito import app
from tito.helpers import buttons

# --------------------------------------------------------------------------
# Reply pools — picked at random each time so it never feels repetitive.
# Formatted as a title + ● bullet points inside a blockquote, matching the
# bot's own visual style (see the ● lines used in locales/*.json).
# --------------------------------------------------------------------------

BOT_REPLIES = [
    "<blockquote><b>😒 ليا اسم زيك ياض</blockquote>",

    "<blockquote><b>☺️ اسمي كريزه يبغل</blockquote>",

    "<blockquote><b>اي يمعلم عايز اي متصدعناش 😪</blockquote>",

    "<blockquote><b>بوت؟ أبوته 😁😂 سوري اندمجت</blockquote>",

    "<blockquote><b>نيعمم عايز ايي👀</blockquote>",
]

DI_REPLIES = [
    "<blockquote><b>لا اله الا الله</b> 🕋</blockquote>",
    "<blockquote><b>صلي علي النبي</b> 🌙</blockquote>",
    "<blockquote><b>اللهم صلي وسلم وبارك على سيدنا محمد</b> 💚</blockquote>",
]

CRAZY_REPLIES = [
    "<blockquote>قلب الكريزه يناس 🫶🏻</blockquote>",

    "<blockquote><b>قلبهااا 👀🫀</blockquote>",

    "<blockquote><b>أكريزووو 😁😂</blockquote>",

    "<blockquote><b>قلبهااا مووووه 💋</blockquote>",

    "<blockquote><b>أكريزو 😁😂</blockquote>",
]


@app.on_message(
    filters.text
    & filters.regex(r"(?i)\bبوت\b")
    & ~filters.bot
    & ~filters.via_bot
    & ~app.bl_users,
    group=40,
)
async def _bot_keyword(_, m: types.Message):
    try:
        await m.reply_text(random.choice(BOT_REPLIES), quote=True)
    except Exception:
        pass


@app.on_message(
    filters.text
    & filters.regex(r"(?i)\bكريز[هي]\b")
    & ~filters.bot
    & ~filters.via_bot
    & ~app.bl_users,
    group=41,
)
async def _crazy_keyword(_, m: types.Message):
    try:
        await m.reply_text(random.choice(CRAZY_REPLIES), quote=True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# A lone "." (dot/period) message -> replies with one of the religious
# phrases, each carrying its own distinctive emoji.
# --------------------------------------------------------------------------
@app.on_message(
    filters.text
    & filters.regex(r"^\s*\.\s*$")
    & ~filters.bot
    & ~filters.via_bot
    & ~app.bl_users,
    group=43,
)
async def _dot_keyword(_, m: types.Message):
    try:
        await m.reply_text(random.choice(DI_REPLIES), quote=True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# "المطور" (developer) keyword -> replies with a button pointing at @xcode000
# --------------------------------------------------------------------------
@app.on_message(
    filters.text
    & filters.regex(r"(?i)\bالمطور\b")
    & ~filters.bot
    & ~filters.via_bot
    & ~app.bl_users,
    group=42,
)
async def _developer_keyword(_, m: types.Message):
    try:
        await m.reply_photo(
            photo="https://files.catbox.moe/5loc28.png",
            reply_markup=buttons.ikm(
                [[buttons.ikb(
                    text="المطور",
                    url="https://t.me/xcode000",
                    style=enums.ButtonStyle.SUCCESS,
                )]]
            ),
            quote=True,
        )
    except Exception:
        pass
