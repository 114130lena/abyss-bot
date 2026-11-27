"""Loopback-only bridge for signed Discord interaction webhooks."""
import os
import time
import json
from aiohttp import web
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from .store import CHANNEL_ID

async def start_bridge(bot):
    key=VerifyKey(bytes.fromhex(os.environ["DISCORD_APPLICATION_PUBLIC_KEY"]))
    seen={}
    async def receive(request):
        body=await request.read()
        try:
            stamp=request.headers["X-Signature-Timestamp"]
            sig=bytes.fromhex(request.headers["X-Signature-Ed25519"])
            if abs(time.time()-int(stamp))>60:
                return web.Response(status=401)
            key.verify(stamp.encode("ascii")+body,sig)
            data=json.loads(body)
            if (data.get("type") not in (3,5)
                or not str((data.get("data") or {}).get("custom_id","")).startswith("lp:")
                or str(data.get("channel_id"))!=str(CHANNEL_ID)
                or str(data.get("guild_id"))!=os.environ["DISCORD_GUILD_ID"]
                or str(data.get("application_id"))!=str(bot.application_id)):
                return web.Response(status=403)
            iid=str(data["id"])
        except (KeyError, ValueError, TypeError, BadSignatureError):
            return web.Response(status=401)
        if not bot.is_ready():
            return web.Response(status=503)
        now=time.monotonic()
        for old in [k for k,v in seen.items() if now-v>120]:
            seen.pop(old,None)
        if iid in seen:
            return web.Response(status=202)
        # discord.py's Gateway parser also dispatches views and modals from
        # authenticated HTTP payloads. Pin the supported library release.
        bot._connection.parse_interaction_create(data)
        seen[iid]=now
        return web.Response(status=202)
    app=web.Application(client_max_size=16384)
    app.router.add_post("/interactions",receive)
    runner=web.AppRunner(app,access_log=None)
    await runner.setup()
    await web.TCPSite(runner,"127.0.0.1",8768).start()
    return runner
