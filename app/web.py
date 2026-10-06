"""HTTP-приёмник вебхука Тильды."""
import json
import logging

from aiohttp import web

from . import tilda
from .bot import App

log = logging.getLogger(__name__)


async def _read_payload(request: web.Request) -> dict:
    if request.content_type == "application/json":
        return await request.json()
    data = dict(await request.post())
    # Тильда может прислать всё одним JSON-полем
    if len(data) == 1 and next(iter(data.values()), "").startswith("{"):
        try:
            return json.loads(next(iter(data.values())))
        except json.JSONDecodeError:
            pass
    return data


def build_web(app: App) -> web.Application:
    async def tilda_hook(request: web.Request) -> web.Response:
        token = app.settings.tilda_token
        if token and request.query.get("token") != token:
            return web.Response(status=403, text="forbidden")
        data = await _read_payload(request)
        if data.get("test"):  # проверочный запрос при подключении вебхука
            return web.Response(text="ok")
        try:
            order = tilda.parse(data)
            await app.new_order(order)
        except Exception:
            log.exception("failed to process tilda payload: %s", data)
            return web.Response(status=500, text="error")
        return web.Response(text="ok")

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    web_app = web.Application()
    web_app.router.add_post("/tilda", tilda_hook)
    web_app.router.add_get("/", health)
    return web_app
