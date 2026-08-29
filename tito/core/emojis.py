# ==============================================================================
# emojis.py - Custom (Premium) Emoji Registry
# ==============================================================================
# Central place to plug in Telegram Premium "custom emoji" IDs.
#
# HOW TO USE:
#   1. Get the custom_emoji_id for any premium emoji (forward a message that
#      contains it to @idcustomemojibot, or grab it from the raw message
#      entities via the Bot API / MTProto).
#   2. Paste that ID as the value for the matching emoji below.
#   3. Leave it as "" (empty string) to keep using the normal/original emoji.
#
# Every string coming out of the language system (lang.py) is automatically
# passed through `apply_custom_emojis()`, so you never need to touch the
# locale JSON files or plugin code again - just fill in IDs here.
#
# If an ID is missing, invalid, or the bot/account isn't Premium, Telegram
# will simply render the fallback unicode emoji that is embedded inside the
# <emoji> tag, so nothing ever breaks.
# ==============================================================================

import re

# emoji -> custom_emoji_id ("" = not set yet, keeps the classic emoji)
CUSTOM_EMOJIS: dict[str, str] = {
    # status / results
    "✅": "5875150442110916703",
    "❌": "5875227622673226273",
    "⚠️": "5875377310873425801",
    "🚫": "5872815547564955937",
    "🛑": "5938149692423541565",
    "🔴": "5965401908456722374",
    "🟢": "5965034821896900637",
    # music / playback
    "🎵": "5217933090483098080",
    "⬇️": "5872697143906540161",
    "❤️": "5976364782414993274",
    "💖": "5976364782414993274",
    "🎧": "5983038328369322029",
    "🪄": "5300801190219493634",
    "🔁": "5256131095094652290",
    "🔂": "5379577431663339538",
    "🔄": "5377724793225243312",
    # games
    "🎮": "5201935999457860851",
    "🎲": "5942627642506222527",
    "🎯": "5935837346455887329",
    "🎰": "5978729161911439683",
    "🎳": "5978729161911439683",
    "🏀": "5269466895534276273",
    "⚽": "5431895506332689488",
    "🏆": "5801086806287982348",
    "🎉": "5208541126583136130",
    # people / admin
    "👑": "5918220984040560357",
    "👤": "5936277975740718749",
    "👥": "5816630842688018483",
    "🤖": "5247250550130486334",
    "🤵": "5816576382502704052",
    "👋": "4958832114540741368",
    # utility / misc
    "🌐": "5380055294019659715",
    "🌙": "5936287931474910073",
    "🌟": "5872967623766972414",
    "✨": "4958489311726011319",
    "⚡": "4958479549265347295",
    "📁": "5332586662629227075",
    "📊": "5380016506170016358",
    "📋": "5839155790081953914",
    "📎": "5769491021108877268",
    "📞": "5440380763981753829",
    "📢": "6051046545636201622",
    "📤": "5962821629544241710",
    "📦": "5832281389982029630",
    "🔌": "6001109995972727648",
    "🔐": "6037406760296255843",
    "🔒": "5301087952300946072",
    "🔔": "4956290155326473271",
    "🔖": "4958621433509970793",
    "🔗": "4958689671950369798",
    "🔧": "5201984395149350592",
    "🔍": "5934021395628431123",
    "➕": "4956507094124594921",
    "➖": "5390831560238839648",
    "➜": "6050603223406874700",
    "➡️": "6051134828688969922",
    "➤": "",
    "🔙": "5332819376842226496",
    "❓": "4956706659780003072",
    "⛔": "5301255425960721217",
    # faces / reactions (keyword auto-replies, etc.)
    "☺️": "5802941411821099047",
    "😁": "5875353151682386339",
    "😂": "5778400119015609832",
    "😒": "5778439473800941307",
    "😪": "5821431976174819987",
    "👀": "4958617898751886363",
    "💋": "6046198060134960956",
    "🫶🏻": "5875450905138043099",
    "🫀": "5949451958992508085",
    # media / files
    "🎙️": "4958970970833421029",
    "🎞️": "5321505140199418151",
    "🎬": "5321153635780961305",
    "📄": "5839163800195961566",
    "📍": "4958728373900674046",
    "📷": "5801027419775180076",
    "🖼️": "5800854852284194743",
    # misc
    "🍪": "4956390086330549100",
    "🏓": "5978729161911439683",
    "👮": "5868607544766764331",
    "🔑": "5935804373991954653",
    "🔕": "5300903831347940030",
    "🕌": "5933979322128799856",
    "🆘": "5442724901297336560",
    "⏩": "5852578061098163396",
    "💚" : "5936196302642616099",
    "🕋" : "5936277026552945172",
    "🎛️" : "4956507094124594921",
    "✨" : "4958489311726011319",
}

# pre-compiled pattern (built once) matching any emoji we know about.
# Longest keys first, so a multi-codepoint emoji (e.g. one with a variation
# selector or skin-tone modifier) is never shadowed by a shorter prefix.
_EMOJI_RE = re.compile(
    "|".join(re.escape(e) for e in sorted(CUSTOM_EMOJIS, key=len, reverse=True) if e)
)


def apply_custom_emojis(text: str) -> str:
    """Swap known unicode emoji inside `text` for Telegram custom-emoji tags.

    Any emoji without a configured ID is left untouched (classic fallback).
    Safe to call on plain strings that contain no emoji at all.
    """
    if not isinstance(text, str) or not text:
        return text

    def _sub(match: "re.Match") -> str:
        char = match.group(0)
        custom_id = CUSTOM_EMOJIS.get(char)
        if not custom_id:
            return char
        return f'<emoji id="{custom_id}">{char}</emoji>'

    return _EMOJI_RE.sub(_sub, text)