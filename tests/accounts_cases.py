"""Accounts an extension signs in to (extension_host/accounts.py): the sign-in address, the code traded for a
refresh token kept in data/.env, access tokens renewed and kept, a new refresh token kept, one the service no
longer takes removed, and what is refused. A fake host and a fake service in a throwaway data directory.

    uv run python tests/accounts_cases.py
"""
import asyncio
from urllib.parse import parse_qs, parse_qsl, urlsplit

from harness import expect, finish, scratch_data

DATA = scratch_data("winston-accounts-")

import httpx  # noqa: E402

from winston.api import secrets  # noqa: E402
from winston.extension_host import accounts  # noqa: E402

ACCOUNT = {"var": "MUSIC_ACCOUNT", "authorize": "https://music.example/authorize", "token": "https://music.example/token",
           "scopes": ["library-read", "top-read"], "client_id": "client_id", "client_secret": "client_secret",
           "label": "Music account"}
REDIRECT = "http://127.0.0.1:8700/api/accounts/callback"


class Host:
    def __init__(self, network=("music.example",), keys=("app-id", "app-secret")):
        self.described = {"music": {"manifest": {"permissions": {"network": list(network)}}, "settings": {"schema": {
            "properties": {"account": {"type": "boolean", "account": ACCOUNT}, "client_id": {"type": "string"}}}}}}
        self.keys = dict(zip(("client_id", "client_secret"), keys))

    def secret_values(self, ext_id):
        return self.keys


class Service:
    def __init__(self):
        self.grants, self.refresh_given, self.refuse = [], "refresh-1", False

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode()))
        self.grants.append((request.headers.get("authorization"), form))
        if self.refuse:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token revoked"})
        n = len(self.grants)
        body = {"access_token": f"access-{n}", "expires_in": 3600}
        if form["grant_type"] == "authorization_code" or self.refresh_given != form.get("refresh_token"):
            body["refresh_token"] = self.refresh_given
        return httpx.Response(200, json=body)


async def main():
    host, service = Host(), Service()
    accounts._transport = httpx.MockTransport(service.handle)
    url = accounts.start(host, "music", "account", REDIRECT)
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    expect("the sign-in address: the service's, with the app, the way back, the scopes and a state",
           url.startswith(ACCOUNT["authorize"] + "?") and q["client_id"] == "app-id" and q["redirect_uri"] == REDIRECT
           and q["scope"] == "library-read top-read" and q["response_type"] == "code" and len(q["state"]) > 20, q)

    ext_id, var = await accounts.finish(host, q["state"], "code-1")
    auth, form = service.grants[-1]
    expect("the code is traded with the app's keys and the same way back",
           auth == httpx.BasicAuth("app-id", "app-secret")._auth_header and form == {
               "grant_type": "authorization_code", "code": "code-1", "redirect_uri": REDIRECT}, (auth, form))
    expect("the refresh token is kept in data/.env, under the account's variable",
           (ext_id, var) == ("music", "MUSIC_ACCOUNT") and secrets.value(var) == "refresh-1"
           and "refresh-1" in (DATA / ".env").read_text(), secrets.value(var))
    for name, state in [("a state finishes once", q["state"]), ("a made-up state", "x" * 32),
                        ("a state that is a path", "../../.env"), ("an empty state", "")]:
        try:
            await accounts.finish(host, state, "code-1")
            expect(f"refused: {name}", False)
        except accounts.AccountError as e:
            expect(f"refused: {name}", "not started here" in str(e), e)
    url = accounts.begin(host.described["music"], "music", "account", host.keys, REDIRECT)   # winston ext connect
    state = parse_qs(urlsplit(url).query)["state"][0]
    expect("a sign-in started from the terminal is a state file, readable by its owner only",
           oct((DATA / "state" / "accounts" / f"{state}.json").stat().st_mode & 0o777) == "0o600")
    service.refresh_given = "refresh-0"
    await accounts.finish(host, state, "code-2")
    expect("and finishes the same way", secrets.value(var) == "refresh-0" and service.grants[-1][1]["code"] == "code-2"
           and not list((DATA / "state" / "accounts").glob("*.json")), secrets.value(var))
    service.refresh_given = "refresh-1"
    secrets.write(var, "refresh-1")

    expect("the token from the sign-in is handed out as it is",
           await accounts.token(host, "music", "account") == f"access-{len(service.grants)}")
    accounts.forget(var)
    grants = len(service.grants)
    t = await accounts.token(host, "music", "account")
    expect("then renewed with the refresh token, once, and kept",
           t == f"access-{grants + 1}" and service.grants[-1][1] == {"grant_type": "refresh_token", "refresh_token": "refresh-1"}
           and await accounts.token(host, "music", "account") == t and len(service.grants) == grants + 1, service.grants[-1])
    service.refresh_given = "refresh-2"
    accounts.forget(var)
    await accounts.token(host, "music", "account")
    expect("a new refresh token the service hands out is kept", secrets.value(var) == "refresh-2", secrets.value(var))
    service.refuse = True
    accounts.forget(var)
    t = await accounts.token(host, "music", "account")
    expect("one the service no longer takes is removed: not connected", t is None and not secrets.value(var))

    for name, h, why in [("a token host outside its network permission", Host(network=("other.example",)), "network"),
                         ("an app without its keys", Host(keys=("", "")), "client ID")]:
        try:
            accounts.start(h, "music", "account", REDIRECT)
            expect(f"refused: {name}", False)
        except accounts.AccountError as e:
            expect(f"refused: {name}", why in str(e), e)
    try:
        accounts.start(host, "music", "client_id", REDIRECT)
        expect("refused: a field that is no account", False)
    except accounts.AccountError as e:
        expect("refused: a field that is no account", "no account setting" in str(e), e)
    expect("not connected, no token and no call", await accounts.token(host, "music", "account") is None
           and service.grants[-1][1]["grant_type"] == "refresh_token" and len(service.grants) == grants + 3)


asyncio.run(main())
finish()
