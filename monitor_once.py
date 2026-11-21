import json, os, base64
from pathlib import Path
import aiohttp
import asyncio

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8835091053")
SC_CLIENT_ID = os.environ["SOUNDCLOUD_CLIENT_ID"]
SC_CLIENT_SECRET = os.environ["SOUNDCLOUD_CLIENT_SECRET"]
PROFILE_URL = os.environ.get("SOUNDCLOUD_PROFILE_URL", "https://soundcloud.com/skellx")
STATE = Path("state.json")

async def main():
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        auth = aiohttp.BasicAuth(SC_CLIENT_ID, SC_CLIENT_SECRET)
        async with s.post("https://secure.soundcloud.com/oauth/token", auth=auth,
                          data={"grant_type":"client_credentials"}) as r:
            raw = await r.text()
            if r.status >= 400: raise RuntimeError(f"SoundCloud OAuth {r.status}: {raw[:400]}")
            token = (await r.json())["access_token"]
        async with s.get("https://api.soundcloud.com/resolve", params={"url":PROFILE_URL},
                         headers={"Authorization":f"OAuth {token}"}) as r:
            raw = await r.text()
            if r.status >= 400: raise RuntimeError(f"SoundCloud API {r.status}: {raw[:400]}")
            p = await r.json()
        fields = ("id","username","permalink_url","description","city","country",
                  "followers_count","followings_count","track_count","playlist_count",
                  "public_favorites_count","avatar_url")
        cur = {k:p[k] for k in fields if k in p}
        if not cur.get("id"): raise RuntimeError("SoundCloud не вернул ID профиля")
        old = json.loads(STATE.read_text()) if STATE.exists() else None
        labels = {"username":"Имя профиля","description":"Описание","city":"Город",
                  "country":"Страна","followers_count":"Подписчики","followings_count":"Подписки",
                  "track_count":"Треки","playlist_count":"Плейлисты",
                  "public_favorites_count":"Публичные лайки","avatar_url":"Аватар"}
        if old is None:
            msg = "Монитор SoundCloud запущен.\nПрофиль: " + cur.get("permalink_url", PROFILE_URL)
        else:
            diff = [f"{labels[k]}: {old.get(k)} → {cur.get(k)}" for k in labels
                    if k in old and k in cur and old[k] != cur[k]]
            msg = ("Изменения в SoundCloud профиле:\n" + "\n".join("• "+x for x in diff)
                   + "\n\n" + cur.get("permalink_url", PROFILE_URL)) if diff else None
        STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2))
        if msg:
            async with s.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                              json={"chat_id":CHAT_ID,"text":msg,"disable_web_page_preview":True}) as r:
                raw = await r.text()
                if r.status >= 400: raise RuntimeError(f"Telegram API {r.status}: {raw[:400]}")
        print("Проверка завершена; изменения:", bool(msg and old is not None))

if __name__ == "__main__":
    asyncio.run(main())
