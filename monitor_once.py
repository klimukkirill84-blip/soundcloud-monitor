import json
import os
import re
import asyncio
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8835091053")
PROFILE_URL = os.environ.get("SOUNDCLOUD_PROFILE_URL", "https://soundcloud.com/skellx")
STATE = Path("state.json")


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
    # SoundCloud embeds public profile data in its server-rendered hydration JSON.
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

    # Safe fallback: public Open Graph metadata (counts may not be available).
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


async def send_message(session, message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with session.post(url, json={
        "chat_id": CHAT_ID,
        "text": message,
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
        fields = (
            "id", "username", "permalink_url", "description", "city", "country",
            "followers_count", "followings_count", "track_count", "playlist_count",
            "public_favorites_count", "avatar_url",
        )
        current = {key: profile[key] for key in fields if key in profile}
        if not current.get("username") and not current.get("id"):
            raise RuntimeError("Публичная страница не содержит данных профиля; состояние не изменено")

        old = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
        labels = {
            "username": "Имя профиля", "description": "Описание", "city": "Город",
            "country": "Страна", "followers_count": "Подписчики",
            "followings_count": "Подписки", "track_count": "Треки",
            "playlist_count": "Плейлисты", "public_favorites_count": "Публичные лайки",
            "avatar_url": "Аватар",
        }

        if old is None:
            message = "Монитор SoundCloud запущен.\nПрофиль: " + current.get("permalink_url", PROFILE_URL)
        else:
            changes = []
            for key, label in labels.items():
                if key in old and key in current and old[key] != current[key]:
                    before, after = old[key], current[key]
                    if key == "avatar_url":
                        before, after = "изменён", "изменён"
                    changes.append(f"{label}: {before} → {after}")
            message = (
                "Изменения в SoundCloud профиле:\n"
                + "\n".join("• " + change for change in changes)
                + "\n\n" + current.get("permalink_url", PROFILE_URL)
            ) if changes else None

        # Only save a complete successful response, never an error/empty page.
        STATE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        if message:
            await send_message(session, message)
        print("Проверка завершена; изменения:", bool(message and old is not None))


if __name__ == "__main__":
    asyncio.run(main())
