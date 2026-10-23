"""Home Assistant by signing in (docs/setup.md, the wizard's Home Assistant step). Found on the network
(`_home-assistant._tcp`, then homeassistant.local), or typed; then Home Assistant's own login in the
browser, which comes back with a code. With it winstond makes a long-lived token named Winston on that
account (one made earlier is replaced, since a name is taken once), switches on the Model Context
Protocol Server if it is off and the account is an administrator's, and drops the sign-in's own session.
The token goes to data/.env and nowhere else: not to a log, a page or an API answer."""
import asyncio
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import aiohttp

from winston.providers.home_assistant import TIMEOUT, _Socket

TOKEN_NAME = "Winston"
LIFESPAN_DAYS = 3650            # what Home Assistant's own profile page gives a long-lived token
STATE_SECS = 600                # as long as Home Assistant keeps a code
SERVICE = "_home-assistant._tcp.local."
CALLBACK = "/api/providers/home_assistant/signin/callback"


@dataclass
class Pending:
    url: str                    # Home Assistant, as the browser and winstond both reach it
    client_id: str              # this page's address, which Home Assistant sends the browser back to
    back: str                   # the page that started it
    at: float


@dataclass
class SignedIn:
    token: str
    name: str
    admin: bool
    mcp: str                    # "on", "switched on", or "off" (an administrator has to)


_pending: dict[str, Pending] = {}


def start(url: str, origin: str, back: str) -> str:
    """The address of Home Assistant's login for a new sign-in. Its state stays here for the way back, once
    and for ten minutes: a code arriving with any other state is refused."""
    now = time.monotonic()
    for k in [k for k, p in _pending.items() if now - p.at > STATE_SECS]:
        del _pending[k]
    state = secrets.token_urlsafe(24)
    url = url.strip().rstrip("/")
    _pending[state] = Pending(url, f"{origin}/", back, now)
    return f"{url}/auth/authorize?" + urlencode({"response_type": "code", "client_id": f"{origin}/",
                                                  "redirect_uri": f"{origin}{CALLBACK}", "state": state})


def take(state: str) -> Pending | None:
    p = _pending.pop(state, None)
    return p if p and time.monotonic() - p.at <= STATE_SECS else None


async def finish(p: Pending, code: str) -> SignedIn:
    """The code for a session, the session for a long-lived token named Winston, the MCP server switched on,
    and the session dropped again whatever happened."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT * 6)) as s:
        r = await s.post(f"{p.url}/auth/token", data={"grant_type": "authorization_code", "code": code,
                                                      "client_id": p.client_id})
        if r.status != 200:
            raise RuntimeError(f"Home Assistant refused the sign-in's code ({r.status})")
        session = await r.json()
        try:
            async with _Socket(s, p.url, session["access_token"]) as ws:
                user = await ws.call("auth/current_user")
                for t in await ws.call("auth/refresh_tokens"):
                    if t.get("client_name") == TOKEN_NAME and t.get("type") == "long_lived_access_token":
                        await ws.call("auth/delete_refresh_token", refresh_token_id=t["id"])
                token = await ws.call("auth/long_lived_access_token", client_name=TOKEN_NAME, lifespan=LIFESPAN_DAYS)
            mcp = await _mcp_server(s, p.url, session["access_token"], bool(user.get("is_admin")))
        finally:
            if session.get("refresh_token"):
                await s.post(f"{p.url}/auth/revoke", data={"token": session["refresh_token"]})
    return SignedIn(token, user.get("name") or "", bool(user.get("is_admin")), mcp)


async def _mcp_server(s: aiohttp.ClientSession, url: str, access: str, admin: bool) -> str:
    """Home Assistant's MCP server, through its own config flow with Assist, as its integration page adds it."""
    h = {"Authorization": f"Bearer {access}"}
    conf = await (await s.get(f"{url}/api/config", headers=h)).json()
    if "mcp_server" in conf.get("components", []):
        return "on"
    if not admin:
        return "off"
    flow = await (await s.post(f"{url}/api/config/config_entries/flow", headers=h,
                               json={"handler": "mcp_server", "show_advanced_options": False})).json()
    if flow.get("type") == "form":
        flow = await (await s.post(f"{url}/api/config/config_entries/flow/{flow['flow_id']}", headers=h,
                                   json={"llm_hass_api": ["assist"]})).json()
    if flow.get("type") == "create_entry":
        return "switched on"
    return "on" if flow.get("reason") in ("single_instance_allowed", "already_configured") else "off"


async def discover(wait: float = 2.0) -> list[dict]:
    """Home Assistants that announce themselves here, by the address they give for this network; else
    homeassistant.local where the system resolves it. mDNS does not cross subnets, so none may be found."""
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    names: list[str] = []

    def seen(zeroconf, service_type: str, name: str, state_change: ServiceStateChange) -> None:
        if state_change is ServiceStateChange.Added:
            names.append(name)
    found: dict[str, dict] = {}
    azc = AsyncZeroconf()
    browser = AsyncServiceBrowser(azc.zeroconf, [SERVICE], handlers=[seen])
    try:
        await asyncio.sleep(wait)
        for name in names:
            info = AsyncServiceInfo(SERVICE, name)
            if not await info.async_request(azc.zeroconf, 1500):
                continue
            props = {k.decode(): (v or b"").decode() for k, v in info.properties.items()}
            addresses = info.parsed_addresses()
            url = props.get("internal_url") or props.get("base_url") or (
                f"http://{addresses[0]}:{info.port}" if addresses else "")
            if url:
                found[url.rstrip("/")] = {"url": url.rstrip("/"), "version": props.get("version") or None,
                                          "name": props.get("location_name") or name.removesuffix(f".{SERVICE}")}
    finally:
        await browser.async_cancel()
        await azc.async_close()
    if not found:
        url = "http://homeassistant.local:8123"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                manifest = await (await s.get(f"{url}/manifest.json")).json(content_type=None)
            if "Home Assistant" in str(manifest.get("name", "")):
                found[url] = {"url": url, "name": "homeassistant.local", "version": None}
        except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError):
            pass
    return list(found.values())
