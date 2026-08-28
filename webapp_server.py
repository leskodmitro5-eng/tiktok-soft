import os
import asyncio
import logging
from pathlib import Path
from aiohttp import web

logger = logging.getLogger("WebAppServer")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "webapp"


async def handle_index(request):
    """Serves index.html for Telegram WebApp."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return web.FileResponse(str(index_file))
    return web.Response(
        text="<!DOCTYPE html><html><head><title>TikTok Soft AI Studio</title></head><body style='background:#0f172a;color:#fff;font-family:sans-serif;text-align:center;padding:50px;'><h1>🎬 TikTok Soft AI Studio</h1><p>Telegram Bot WebApp Service is Live & Healthy ✅</p></body></html>",
        content_type="text/html"
    )


def create_webapp_app():
    app = web.Application()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.router.add_get("/", handle_index)
    try:
        app.router.add_static("/static/", path=str(STATIC_DIR), name="static")
    except Exception as e:
        logger.warning(f"Static directory mounting skipped: {e}")
    return app


async def start_webapp_server(host: str = "0.0.0.0", port: int = 8085):
    """Starts async web server in the background."""
    app = create_webapp_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"Telegram Mini App Studio live at http://{host}:{port}/")
    return runner


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    runner = loop.run_until_complete(start_webapp_server("0.0.0.0", 8085))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        loop.run_until_complete(runner.cleanup())
