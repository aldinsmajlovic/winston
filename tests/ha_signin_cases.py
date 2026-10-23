"""Home Assistant by signing in (src/winston/providers/ha_signin.py, docs/setup.md): a throwaway winstond
on a new install's config against tests/fake_ha.py, the browser's part played by following the redirects.
Found through zeroconf as Home Assistant announces itself; signed in, the token named Winston saved in
data/.env and in no log or API answer; the MCP server switched on; the session dropped; the device list not
applied (a first connect); signing in again replacing the token; a wrong or used state refused; a cancelled
sign-in said; an account that is not an administrator's warned and the MCP server left to one.

    uv run python tests/ha_signin_cases.py
"""
import asyncio
import socket
from urllib.parse import parse_qs, urlsplit

from harness import check, finish_checks, free_port, scratch_data, serve, winstond_data

data = scratch_data("winston-ha-signin-")

import httpx  # noqa: E402
import yaml  # noqa: E402
from loguru import logger  # noqa: E402

from fake_ha import FakeHA  # noqa: E402

API, HA = free_port(), free_port()
said: list[str] = []
logger.add(said.append, level="DEBUG")
# A new install: Home Assistant on at the example's address, with no token yet.
winstond_data(data, {"llm": {"model": "fake", "base_url": "http://127.0.0.1:9/v1"},
                     "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False},
                                  "discord": {"enabled": False}},
                     "providers": {"home_assistant": {"url": "http://homeassistant.local:8123", "token_env": "HA_TOKEN",
                                                      "expose": {}}},
                     "control": {"host": "127.0.0.1", "port": API}})
(data / ".env").write_text("")


async def sign_in(api: httpx.AsyncClient, url: str, back: str = "/setup/home_assistant") -> tuple[str, dict]:
    """The page's Sign in, then the browser: Home Assistant's login (which signs in at once), then back."""
    authorize = (await api.post("/api/providers/home_assistant/signin", json={"url": url, "back": back})).json()["authorize"]
    async with httpx.AsyncClient() as browser:
        to_us = (await browser.get(authorize)).headers["location"]
        r = await browser.get(to_us)
    where = r.headers.get("location", "")
    return authorize, {"status": r.status_code, "path": urlsplit(where).path,
                       **{k: v[0] for k, v in parse_qs(urlsplit(where).query).items()}}


def env_token() -> str:
    from dotenv import dotenv_values
    return dotenv_values(data / ".env").get("HA_TOKEN") or ""


async def main():
    from zeroconf import ServiceInfo
    from zeroconf.asyncio import AsyncZeroconf

    from winston.api.app import create_app
    from winston.config import load

    async with FakeHA(HA, mcp_on=False) as fake:
        app = create_app(load(), autostart=False)
        stop = await serve(app, API)
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{API}", timeout=30)
        try:
            # Found as Home Assistant announces itself, by the address it gives.
            azc = AsyncZeroconf()
            info = ServiceInfo("_home-assistant._tcp.local.", "Fake home._home-assistant._tcp.local.",
                               addresses=[socket.inet_aton("127.0.0.1")], port=HA, server="fake-ha.local.",
                               properties={"location_name": "Fake home", "version": "2026.9.3",
                                           "internal_url": fake.url, "base_url": fake.url})
            await azc.async_register_service(info)
            found = (await api.get("/api/providers/home_assistant/discover")).json()
            await azc.async_unregister_service(info)
            await azc.async_close()
            check("found through zeroconf, by its name and address", [f for f in found if f["url"] == fake.url],
                  [{"url": fake.url, "name": "Fake home", "version": "2026.9.3"}])

            authorize, back = await sign_in(api, fake.url)
            q = parse_qs(urlsplit(authorize).query)
            check("Home Assistant's login, for this page and back to it",
                  (q["client_id"], q["redirect_uri"], q["response_type"]),
                  ([f"http://127.0.0.1:{API}/"], [f"http://127.0.0.1:{API}/api/providers/home_assistant/signin/callback"],
                   ["code"]))
            check("signed in: back to the wizard, as whom, an administrator, the MCP server switched on",
                  back, {"status": 303, "path": "/setup/home_assistant", "signin": "done", "name": "Someone",
                         "admin": "1", "mcp": "switched on"})
            token = env_token()
            winston = [r for r in fake.refresh.values() if r["type"] == "long_lived_access_token"]
            check("a long-lived token named Winston, saved in data/.env, which Home Assistant takes",
                  ([r["client_name"] for r in winston], token in fake.tokens, token.startswith("fake-llat")),
                  (["Winston"], True, True))
            check("the sign-in's own session dropped", [r for r in fake.refresh.values() if r["type"] == "normal"], [])
            check("the MCP server switched on with Assist", (fake.mcp_on, [w for w in fake.writes if w["type"] ==
                                                                           "config_entries/mcp_server"]),
                  (True, [{"type": "config_entries/mcp_server", "llm_hass_api": ["assist"]}]))
            cfg = yaml.safe_load((data / "config.yaml").read_text())["providers"]["home_assistant"]
            check("the address saved, Home Assistant on", (cfg["url"], cfg.get("enabled", True)), (fake.url, True))
            check("nothing exposed or un-exposed: a first connect applies no list",
                  [w for w in fake.writes if w["type"].startswith("homeassistant/") or w["type"].startswith("config/")], [])
            r = await api.get("/api/providers/home_assistant/exposure")
            check("and it is connected", (r.json().get("ok"), r.json().get("mcp_server")), (True, True))

            # The token never comes back out.
            answers = [(await api.get(p)).text for p in ("/api/config", "/api/secrets", "/api/status", "/api/setup",
                                                          "/api/providers/home_assistant/exposure")]
            logs = [f.read_text(errors="replace") for f in data.rglob("*.log")] + ["\n".join(said)]
            check("the token in no API answer, log or redirect", [token in t for t in answers + logs + [str(back)]],
                  [False] * (len(answers) + len(logs) + 1))

            # Again: the token is replaced, as Home Assistant takes a name once.
            _, again = await sign_in(api, fake.url, back="/home-assistant")
            new = env_token()
            check("signing in again replaces his token there",
                  (again["path"], again["mcp"], new != token, token in fake.tokens, new in fake.tokens,
                   [r["client_name"] for r in fake.refresh.values()]),
                  ("/home-assistant", "on", True, False, True, ["Winston"]))

            # A code with a state Winston did not give, or one already used, is refused.
            r = await api.get("/api/providers/home_assistant/signin/callback", params={"state": "nope", "code": "x"},
                              follow_redirects=False)
            check("a wrong state is refused, in words", (r.status_code, "not one Winston started" in r.text), (400, True))
            authorize, _ = await sign_in(api, fake.url)
            used = parse_qs(urlsplit(authorize).query)["state"][0]
            r = await api.get("/api/providers/home_assistant/signin/callback", params={"state": used, "code": "x"},
                              follow_redirects=False)
            check("so is a state already used", r.status_code, 400)
            authorize = (await api.post("/api/providers/home_assistant/signin",
                                        json={"url": fake.url, "back": "/home-assistant"})).json()["authorize"]
            state = parse_qs(urlsplit(authorize).query)["state"][0]
            r = await api.get("/api/providers/home_assistant/signin/callback",
                              params={"state": state, "error": "access_denied"}, follow_redirects=False)
            check("a sign-in cancelled in Home Assistant comes back saying so",
                  (r.status_code, "cancelled" in parse_qs(urlsplit(r.headers["location"]).query)["signin_error"][0]),
                  (303, True))
            check("only the pages that start one are gone back to", (await api.post(
                "/api/providers/home_assistant/signin", json={"url": fake.url, "back": "https://elsewhere.example/"})).status_code,
                422)

            # Not an administrator: told so, and its MCP server left to one.
            fake.user["is_admin"], fake.mcp_on = False, False
            _, plain = await sign_in(api, fake.url)
            check("an account that is not an administrator's: signed in, warned, the MCP server left off",
                  (plain["signin"], plain["admin"], plain["mcp"], fake.mcp_on), ("done", "0", "off", False))
        finally:
            await api.aclose()
            await stop()


asyncio.run(main())
finish_checks("Home Assistant sign-in: found through zeroconf, signed in, Winston's token saved and never shown, "
              "the MCP server switched on, the session dropped, no list applied, replaced on a second sign-in, a "
              "wrong or used state refused, a cancel said, a non-administrator warned")
