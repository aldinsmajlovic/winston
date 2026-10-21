"""Accounts an extension signs in to (an `Account` setting): OAuth 2's authorization code, in the browser.

A sign-in starts from Connect on the extension's page (`start`) or from `winston ext connect` on this
machine (`begin`), which a winstond listening beyond this machine needs, since its page may not set keys
then. Either way the service sends the browser back to winstond with a code (`finish`), which it trades
for a refresh token kept in data/.env under the field's variable. Only a sign-in started here finishes:
its one-time state is a file in data/state/accounts/, which only this machine can write. The extension
asks for an access token (`ctx.token`), which winstond renews with the refresh token as it runs out; the
extension never holds the refresh token. The way back is http://127.0.0.1:<port>/api/accounts/callback: a
loopback address is the one plain-http address services such as Spotify accept."""
from __future__ import annotations

import json
import re
import secrets as pysecrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from loguru import logger

from winston.api import secrets
from winston.config import data_dir

CALLBACK = "/api/accounts/callback"
STATE_SECS = 600                    # a sign-in started longer ago than this is not finished
_STATE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_tokens: dict[str, tuple[str, float]] = {}     # variable -> access token, when it runs out
_transport: httpx.AsyncBaseTransport | None = None    # tests set a fake service


class AccountError(Exception):
    """What stops a sign-in or a token, said to the user."""


def fields(described: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Field -> its Account, from an extension's description."""
    props = ((described or {}).get("settings") or {}).get("schema", {}).get("properties", {})
    return {f: p["account"] for f, p in props.items() if isinstance(p.get("account"), dict)}


def _check(url: str, network: list[str]) -> None:
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or ("*" not in network and u.hostname not in network):
        raise AccountError(f"{url} is not an https address on a host in the extension's network permission")


def _account(described: dict[str, Any] | None, field: str) -> dict[str, Any]:
    account = fields(described).get(field)
    if account is None:
        raise AccountError(f"there is no account setting {field}")
    network = (((described or {}).get("manifest") or {}).get("permissions") or {}).get("network") or []
    _check(account["authorize"], network)
    _check(account["token"], network)
    return account


def _app(keys: dict[str, Any], account: dict[str, Any]) -> tuple[str, str]:
    client_id, client_secret = keys.get(account["client_id"]) or "", keys.get(account["client_secret"]) or ""
    if not client_id or not client_secret:
        raise AccountError("save the app's client ID and secret first")
    return client_id, client_secret


def _states() -> Path:
    return data_dir() / "state" / "accounts"


def begin(described: dict[str, Any] | None, ext_id: str, field: str, keys: dict[str, Any], redirect: str) -> str:
    """The service's sign-in address for this account, coming back to `redirect`, with its state written."""
    account = _account(described, field)
    client_id, _ = _app(keys, account)
    folder = _states()
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob("*.json"):
        if now - old.stat().st_mtime > STATE_SECS:
            old.unlink(missing_ok=True)
    state = pysecrets.token_urlsafe(24)
    path = folder / f"{state}.json"
    path.write_text(json.dumps({"ext_id": ext_id, "field": field, "redirect": redirect, "at": now}))
    path.chmod(0o600)
    return account["authorize"] + "?" + urlencode({"response_type": "code", "client_id": client_id, "state": state,
                                                   "redirect_uri": redirect, "scope": " ".join(account["scopes"])})


def start(host, ext_id: str, field: str, redirect: str) -> str:
    """`begin` for a running winstond's extension."""
    return begin(host.described.get(ext_id), ext_id, field, host.secret_values(ext_id), redirect)


async def _grant(account: dict[str, Any], client: tuple[str, str], data: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10, transport=_transport) as http:
        r = await http.post(account["token"], data=data, auth=client)
    body = r.json() if "json" in r.headers.get("content-type", "") else {}
    if r.status_code != 200:
        why = " ".join(str(body[k]) for k in ("error", "error_description") if body.get(k)) or r.text[:120]
        raise AccountError(f"the service said {r.status_code}: {why}")
    return body


async def finish(host, state: str, code: str) -> tuple[str, str]:
    """Trades the code the service sent back for a refresh token, kept in data/.env; the extension and the
    variable it was for. A state not started here, used already or started too long ago is refused."""
    p = None
    if _STATE.match(state):
        path = _states() / f"{state}.json"
        try:
            p = json.loads(path.read_text())
        except (OSError, ValueError):
            pass
        path.unlink(missing_ok=True)
    if p is None or time.time() - p["at"] > STATE_SECS:
        raise AccountError("this sign-in was not started here, or took longer than ten minutes; start it again")
    described = host.described.get(p["ext_id"])
    account = _account(described, p["field"])
    body = await _grant(account, _app(host.secret_values(p["ext_id"]), account),
                        {"grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect"]})
    if not body.get("refresh_token"):
        raise AccountError("the service gave no refresh token")
    secrets.write(account["var"], body["refresh_token"])
    if body.get("access_token"):
        _tokens[account["var"]] = (body["access_token"], time.time() + int(body.get("expires_in") or 3600))
    return p["ext_id"], account["var"]


async def token(host, ext_id: str, field: str) -> str | None:
    """An access token with a minute left at least, renewed with the refresh token; None while the account
    is not connected. A refresh token the service no longer takes is removed, so the page says Connect."""
    account = _account(host.described.get(ext_id), field)
    var = account["var"]
    if (hit := _tokens.get(var)) and hit[1] - time.time() > 60:
        return hit[0]
    if not (refresh := secrets.value(var)):
        return None
    try:
        body = await _grant(account, _app(host.secret_values(ext_id), account),
                            {"grant_type": "refresh_token", "refresh_token": refresh})
    except AccountError as e:
        if "invalid_grant" in str(e):
            logger.warning(f"{ext_id}: the service no longer takes its {field} sign-in ({e}); connect again")
            secrets.remove(var)
            forget(var)
            return None
        raise
    if body.get("refresh_token") and body["refresh_token"] != refresh:     # some services hand out a new one
        secrets.write(var, body["refresh_token"])
    _tokens[var] = (body["access_token"], time.time() + int(body.get("expires_in") or 3600))
    return body["access_token"]


def forget(var: str) -> None:
    """The variable changed (connected again, disconnected): its access token is not used again."""
    _tokens.pop(var, None)
