import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8835091053").strip()
SC_PROFILE_URL = os.getenv("SOUNDCLOUD_PROFILE_URL", "https://soundcloud.com/skellx").strip()
SC_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID", "").strip()
SC_CLIENT_SECRET = os.getenv("SOUNDCLOUD_CLIENT_SECRET", "").strip()
SC_ACCESS_TOKEN = os.getenv("SOUNDCLOUD_ACCESS_TOKEN", "").strip()
POLL_SECONDS = max(300, int(os.getenv("POLL_SECONDS", "900")))
STATE_FILE = Path(os.getenv("STATE_FILE", "state.json"))

API_BASE = "https://api.soundcloud.com"
AUTH_URL = "https://secure.soundcloud.com/oauth/token"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sc-monitor")


class SoundCloud:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.token = SC_ACCESS_TOKEN
        self.token_expires_at = float("inf") if self.token else 0.0

    async def get_token(self) -> str:
        if self.token and time.monotonic() < self.token_expires_at - 60:
            return self.token
        if not SC_CLIENT_ID or not SC_CLIENT_SECRET:
            raise RuntimeError(
                "Нужны SOUNDCLOUD_CLIENT_ID и SOUNDCLOUD_CLIENT_SECRET "
                "(или действующий SOUNDCLOUD_ACCESS_TOKEN) в .env"
            )
        auth = aiohttp.BasicAuth(SC_CLIENT_ID, SC_CLIENT_SECRET)
        async with self.session.post(
            AUTH_URL,
            auth=auth,
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        ) as r:
            body = await r.text()
            if r.status >= 400:
                raise RuntimeError(f"SoundCloud OAuth {r.status}: {body[:500]}")
            data = json.loads(body)
            self.token = data["access_token"]
            self.token_expires_at = time.monotonic() + int(data.get("expires_in", 3600))
            return self.token

    async def api_get(self, path: str, params: dict | None = None) -> Any:
        token = await self.get_token()
        headers = {"Authorization": f"OAuth {token}", "Accept": "application/json"}
        async with self.session.get(
            f"{API_BASE}{path}", params=params, headers=headers
        ) as r:
            body = await r.text()
            if r.status >= 400:
                raise RuntimeError(f"SoundCloud API {r.status}: {body[:500]}")
            return json.loads(body) if body else {}

    async def profile(self) -> dict:
        return await self.api_get("/resolve", {"url": SC_PROFILE_URL})


def read_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def telegram_send(session: aiohttp.ClientSession, message: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    async with session.post(url, json={"chat_id": CHAT_ID, "text": message, "disable_web_page_preview": True}) as r:
        body = await r.text()
        if r.status >= 400:
            raise RuntimeError(f"Telegram API {r.status}: {body[:500]}")


def snapshot(profile: dict) -> dict:
    # Fields are taken only when the API returns them. Unsupported/missing
    # fields are omitted rather than guessed.
    keys = (
        "id", "username", "permalink_url", "description", "city", "country",
        "followers_count", "followings_count", "track_count", "playlist_count",
        "public_favorites_count", "avatar_url",
    )
    return {k: profile.get(k) for k in keys if k in profile}


def changes(old: dict, new: dict) -> list[str]:
    labels = {
        "username": "Имя профиля",
        "description": "Описание",
        "city": "Город",
        "country": "Страна",
        "followers_count": "Подписчики",
        "followings_count": "Подписки",
        "track_count": "Треки",
        "playlist_count": "Плейлисты",
        "public_favorites_count": "Публичные лайки",
        "avatar_url": "Аватар",
    }
    out = []
    for key, label in labels.items():
        if key in old and key in new and old[key] != new[key]:
            out.append(f"{label}: {old[key]} → {new[key]}")
        elif key not in old and key in new:
            out.append(f"{label}: {new[key]}")
    return out


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Заполни TELEGRAM_BOT_TOKEN в .env новым токеном из @BotFather.")
    if not CHAT_ID:
        raise SystemExit("Заполни TELEGRAM_CHAT_ID в .env.")
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        sc = SoundCloud(session)
        first_run = read_state()
        while True:
            try:
                profile = await sc.profile()
                current = snapshot(profile)
                if not current.get("id"):
                    raise RuntimeError("SoundCloud не вернул ID профиля. Проверь URL и доступ API.")
                if first_run is None:
                    save_state(current)
                    first_run = current
                    await telegram_send(
                        session,
                        "Монитор SoundCloud запущен.\n"
                        f"Профиль: {current.get('permalink_url', SC_PROFILE_URL)}\n"
                        "Сохранил исходное состояние. Уведомления будут приходить при обнаружении изменений."
                    )
                    log.info("Initial profile snapshot saved")
                else:
                    diff = changes(first_run, current)
                    if diff:
                        await telegram_send(
                            session,
                            "Изменения в SoundCloud профиле:\n"
                            + "\n".join(f"• {item}" for item in diff)
                            + f"\n\n{current.get('permalink_url', SC_PROFILE_URL)}"
                        )
                        log.info("Changes sent: %s", diff)
                    save_state(current)
                    first_run = current
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("Polling failed")
                try:
                    await telegram_send(session, f"Ошибка мониторинга SoundCloud:\n{e}")
                except Exception:
                    log.exception("Could not send error to Telegram")
            await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
