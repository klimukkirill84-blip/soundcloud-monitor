import json
import os
import re
import asyncio
from html.parser import HTMLParser
from pathlib import Path

import aiohttp

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8835091053")
PROFILE_URL = os.environ.get("SOUNDCLOUD_PROFILE_URL", "https://soundcloud.com/skellx")
STATE = Path("state.json")

FIELDS = (
    "id", "username", "full_name", "permalink_url", "description", "city", "country",
    "website", "created_at", "last_modified", "online",
    "followers_count", "followings_count", "track_count", "playlist_count",
    "public_favorites_count", "avatar_url",
)
LABELS = {
    "username": "Ник", "full_name": "Имя", "description": "Описание",
    "city": "Город", "country": "Страна", "website": "Сайт",
    "created_at": "Дата создания", "last_modified": "Последнее изменение профиля",
    "online": "Онлайн", "followers_count": "Подписчики",
    "followings_count": "Подписки", "track_count": "Треки",
    "playlist_count": "Плейлисты", "public_favorites_count": "Публичные лайки",
    "avatar_url": "Аватар",
}


class MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "meta":
            return
        values = dict(attrs)
        key = values.get("property") or values.get("name")
        if key and values.get("content"):
            self.meta[key.lower()] = values["content"]


def extract_profile(page):
    match = re.search(r"window\.__sc_hydration\s*=\s*(\[.*?\])\s*;", page, re.S)
    if match:
        try:
            hydration = json.loads(match.group(1))
            stack = [hydration]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    if item.get("hydratable") == "user" and isinstance(item.get("data"), dict):
                        return item["data"]
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
        except (json.JSONDecodeError, TypeError):
            pass

    parser = MetaParser()
    parser.feed(page)
    meta = parser.meta
    title = meta.get("og:title")
    if not title:
        raise RuntimeError("Не удалось прочитать публичные данные профиля SoundCloud")
    return {
        "username": title,
        "description": meta.get("og:description", ""),
        "avatar_url": meta.get("og:image", ""),
        "permalink_url": meta.get("og:url", PROFILE_URL),
    }


def display_value(key, value):
    if key == "online":
        return "Да" if value else "Нет"
    if key == "avatar_url":
        return "изменён"
    return str(value)


async def send_message(session, message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with session.post(url, json={
        "chat_id": CHAT_ID,
        "text": message[:4000],
        "disable_web_page_preview": True,
    }) as response:
        body = await response.text()
        if response.status >= 400:
            raise RuntimeError(f"Telegram API {response.status}: {body[:300]}")


async def main():
    timeout = aiohttp.ClientTimeout(total=45)
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ProfileMonitor/1.0)",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(PROFILE_URL, allow_redirects=True) as response:
            if response.status >= 400:
                raise RuntimeError(f"SoundCloud страница вернула HTTP {response.status}")
            page = await response.text()

        profile = extract_profile(page)
        current = {key: profile[key] for key in FIELDS if key in profile and profile[key] is not None}
        if not current.get("username") and not current.get("id"):
            raise RuntimeError("Публичная страница не содержит данных профиля; состояние не изменено")

        old = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
        profile_link = current.get("permalink_url", PROFILE_URL)

        # A different SoundCloud account starts a fresh baseline, not a change report.
        if old is not None:
            old_id, new_id = old.get("id"), current.get("id")
            old_url = (old.get("permalink_url") or "").rstrip("/").lower()
            new_url = (current.get("permalink_url") or PROFILE_URL).rstrip("/").lower()
            if (old_id is not None and new_id is not None and old_id != new_id) or (old_url and new_url and old_url != new_url):
                old = None

        if old is None:
            lines = ["Монитор SoundCloud запущен", "Публичная информация профиля:"]
            for key, label in LABELS.items():
                if key in current and key != "avatar_url":
                    value = current[key]
                    if value != "":
                        lines.append(f"• {label}: {display_value(key, value)}")
            if current.get("avatar_url"):
                lines.append("• Аватар: доступен")
            lines.append(f"Профиль: {profile_link}")
            message = "\n".join(lines)
        else:
            changes = []
            for key, label in LABELS.items():
                before_present = key in old
                after_present = key in current
                if before_present and after_present and old[key] != current[key]:
                    changes.append(f"• {label}: {display_value(key, old[key])} → {display_value(key, current[key])}")
                elif before_present != after_present:
                    value = display_value(key, current[key]) if after_present else "больше не отображается"
                    changes.append(f"• {label}: {value}")
            message = (
                "Обнаружены изменения в SoundCloud профиле:\n"
                + "\n".join(changes)
                + f"\n\nПрофиль: {profile_link}"
            ) if changes else None

        # Send first; only update the baseline after Telegram confirms delivery.
        if message:
            await send_message(session, message)
        STATE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Проверка завершена; найдено изменений:", bool(message and old is not None))


if __name__ == "__main__":
    asyncio.run(main())
