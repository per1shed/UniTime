"""Custom emoji icons from the tgmacicons sticker set."""

# https://t.me/addstickers/tgmacicons

USER = "5316727448644103237"  # 👤
GRADUATION = "5258334872878980409"  # 🎓
CALENDAR = "5258105663359294787"  # 🗓
BELL = "5260325873688518261"  # 🔊
CLOCK = "5258258882022612173"  # ⏲
SUN = "5258089153505009279"  # ☀️
IDEA = "5258216851472654189"  # 💡
ARROW_LEFT = "5258236805890710909"  # ⬅️
ARROW_RIGHT = "5260450573768990626"  # ➡️
ARROW_DOWN = "5258336354642697821"  # ⬇️
PICK = "5886451926995833684"  # ⬇️
CHECK = "5260726538302660868"  # ✅
CROSS = "5258226313285607065"  # ❌


def ce(emoji_id: str, fallback: str) -> str:
    """HTML custom emoji entity for message text."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
