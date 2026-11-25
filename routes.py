"""Small server routes the panel calls: models, test, presets, open folder."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from aiohttp import web

from . import client as lm
from . import engine
from . import presets

_registered = False


def _url(request) -> str:
    return (request.query.get("server_url") or engine.STATE["server_url"] or lm.DEFAULT_URL).strip()


async def models(request: web.Request) -> web.Response:
    """Plain list of model ids for the dropdown (loaded first); ?detail=1 for the panel."""
    url = _url(request)
    try:
        found, endpoint = await asyncio.to_thread(lm.list_models, url, 10.0, engine.api_token())
    except lm.LMStudioError as e:
        if request.query.get("detail"):
            return web.json_response({"ok": False, "error": str(e), "models": []})
        return web.json_response([])
    found.sort(key=lambda m: (m.loaded is False, m.id.lower()))
    if request.query.get("detail"):
        return web.json_response({"ok": True, "endpoint": endpoint, "server_url": url,
                                  "models": [m.as_dict() for m in found]})
    return web.json_response([m.id for m in found])


async def test(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}
    url = str(data.get("server_url") or engine.STATE["server_url"]).strip()
    result = await asyncio.to_thread(lm.test_connection, url, str(data.get("model") or ""), 10.0, engine.api_token())
    if result["reachable"]:
        engine.STATE["server_url"] = url
    return web.json_response(result)


async def preset_list(request: web.Request) -> web.Response:
    found, problems = await asyncio.to_thread(presets.LIBRARY.scan)
    items = [found[n].summary() for n in sorted(found, key=lambda n: (found[n].source != "shipped", n.lower()))]
    return web.json_response({"presets": items, "problems": problems, "user_dir": presets.LIBRARY.user_dir})


async def open_folder(request: web.Request) -> web.Response:
    path = presets.LIBRARY.user_dir
    try:
        os.makedirs(path, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606 - a fixed folder, never a request value
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        return web.json_response({"ok": False, "path": path, "error": str(e)})
    return web.json_response({"ok": True, "path": path})


def register() -> None:
    global _registered
    if _registered:
        return
    from server import PromptServer

    r = PromptServer.instance.routes
    r.get("/lmse/models")(models)
    r.post("/lmse/test")(test)
    r.get("/lmse/presets")(preset_list)
    r.post("/lmse/open_folder")(open_folder)
    _registered = True
