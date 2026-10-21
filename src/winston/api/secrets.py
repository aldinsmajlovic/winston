"""Secrets in data/.env, set and removed from the web UI. The config names variables (fields with
`secret` metadata in the schema, and the keys each MCP server gets in its `env`); this module says which
of them hold a value and writes new values, never reading one back out. Only names the config points at
(or a schema default for such a field) can be written, and only through a winstond that listens on this
machine alone.

Plan §6 rule 5: no secrets in prompts, and none in API responses either."""
import ipaddress
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key, unset_key
from fastapi import HTTPException, Request
from loguru import logger

from winston.config import data_dir
from winston.config.restarts import _deref, restarts_for

NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
# Variables that steer processes rather than hold a credential: never writable from the UI.
DENY = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "TMPDIR", "TEMP", "TMP", "LANG", "TERM", "DISPLAY",
        "VIRTUAL_ENV", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "REQUESTS_CA_BUNDLE", "CLAUDE_CONFIG_DIR",
        "BASH_ENV", "ENV", "IFS", "PS4", "PROMPT_COMMAND", "SHELLOPTS", "BASHOPTS", "GCONV_PATH", "PERL5LIB",
        "PERL5OPT", "RUBYLIB", "RUBYOPT", "JAVA_TOOL_OPTIONS", "EDITOR", "VISUAL", "PAGER", "BROWSER"}
DENY_PREFIX = ("LD_", "DYLD_", "PYTHON", "WINSTON_", "HF_", "UV_", "XDG_", "LC_", "SSL_", "CUDA_", "NVIDIA_",
               "DOCKER_", "TORCH", "OMP_", "CLAUDE_CODE_USE_", "ANTHROPIC_BASE_URL", "PIP_", "NODE_", "GIT_", "SSH_",
               "NPM_", "OPENSSL_", "CURL_")
MAX_LEN = 4096
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def env_file() -> Path:
    return data_dir() / ".env"


def denied(name: str) -> bool:
    return not NAME.match(name) or name in DENY or name.startswith(DENY_PREFIX)


def _walk(value: Any, schema: dict, root: dict, path: list[str], out: list[tuple[list[str], str, dict]]):
    """Every field with `secret` metadata in the config, with its path and the name it holds."""
    schema = _deref(schema, root)
    if "secret" in schema:
        if isinstance(value, str) and value:
            out.append((path, value, schema["secret"]))
        return
    if not isinstance(value, dict):
        return
    props, extra = schema.get("properties"), schema.get("additionalProperties")
    for key, v in value.items():
        if props and key in props:
            _walk(v, props[key], root, path + [key], out)
        elif isinstance(extra, dict):
            _walk(v, extra, root, path + [key], out)


def references(config: dict, schema: dict) -> dict[str, list[list[str]]]:
    """Variable name -> the config paths that name it."""
    found: list[tuple[list[str], str, dict]] = []
    _walk(config, schema, schema, [], found)
    out: dict[str, list[list[str]]] = {}
    for path, name, _ in found:
        out.setdefault(name, []).append(path)
    # The list field itself, not an index in it: restarts() and _set_in step through mappings only.
    for ext_id, ext in (config.get("extensions") or {}).items():
        for name in ((ext or {}).get("mcp") or {}).get("env") or []:
            out.setdefault(name, []).append(["extensions", ext_id, "mcp", "env"])
    return out


def schema_defaults(schema: dict) -> set[str]:
    """The default variable names of every secret field, so a key can be stored before the field is saved."""
    names: set[str] = set()

    def visit(node: Any):
        if isinstance(node, dict):
            if isinstance(node.get("secret"), dict) and node["secret"].get("default"):
                names.add(node["secret"]["default"])
            for v in node.values():
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)
    visit(schema)
    return names


def _set_in(value: dict, path: list[str], new: Any) -> dict:
    head, *rest = path
    out = dict(value)
    out[head] = _set_in(out.get(head) or {}, rest, new) if rest else new
    return out


def restarts(config: dict, schema: dict, paths: list[list[str]]) -> set[str]:
    """What must restart to read a new value of a variable these fields name: the same as changing them."""
    out: set[str] = set()
    for path in paths:
        out |= restarts_for(config, _set_in(config, path, "\0changed"), schema)
    return out


def value(name: str) -> str:
    """Its value, looked up as a process started now would: the environment, then data/.env. So a key
    added to data/.env by hand reaches what starts next without restarting winstond."""
    if v := (os.environ.get(name) or "").strip():
        return v
    f = env_file()
    return (dotenv_values(f).get(name) or "").strip() if f.is_file() else ""


def is_set(name: str) -> bool:
    """Holds a value, looked up as a process started now would."""
    return bool(value(name))


def allowed(name: str, config: dict, schema: dict) -> bool:
    return not denied(name) and (name in references(config, schema) or name in schema_defaults(schema))


def check_value(name: str, value: str):
    if not value.strip():
        raise HTTPException(422, "empty value; remove the secret instead")
    if len(value) > MAX_LEN:
        raise HTTPException(422, f"longer than {MAX_LEN} characters")
    if any(c in value for c in "\n\r\0"):
        raise HTTPException(422, "one line only")
    if "${" in value:
        raise HTTPException(422, "a key cannot hold ${; data/.env would expand it into another variable")


def loopback(host: str) -> bool:
    if host in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _port(u) -> int | None:
    try:
        return u.port or {"http": 80, "https": 443}.get(u.scheme)
    except ValueError:
        return None


def cross_site(request: Request) -> str | None:
    """Why a write request looks like it comes from another site, or None. Applies to every write route: the
    Host must be an address or a local name (a DNS-rebinding page arrives under its own public domain), and
    an Origin, when sent, must be the same origin."""
    host = urlsplit("//" + request.headers.get("host", "")).hostname or ""
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if not (literal or host in LOCAL_HOSTS or "." not in host or host.endswith((".local", ".lan", ".home.arpa"))):
        return f"requests addressed to {host or 'no host'} are refused; use an address or localhost"
    if origin := request.headers.get("origin"):
        o, h = urlsplit(origin), urlsplit(f"{request.url.scheme}://" + request.headers.get("host", ""))
        if (o.scheme, o.hostname, _port(o)) != (h.scheme, h.hostname, _port(h)):
            return "request from another site"
    return None


def guard(request: Request, control_hosts: list[str], body: bool, name: str | None = None):
    """Writes only through a winstond bound to this machine (control.host as started and as saved), from a
    client on it, asked for by a page on it (the Host and Origin headers stop DNS rebinding), with a JSON body
    (which a cross-site form cannot send without CORS). `name`, the variable a key goes under, is said in the
    refusal while the page is open to the network, since the form no longer shows it."""
    if exposed := [h for h in control_hosts if not loopback(h)]:
        if name:        # body: a paste (PUT); else a removal (DELETE)
            what, how = ("pasted", f"Add {name} to") if body else ("removed", f"Remove {name} from")
            raise HTTPException(403, f"Keys can be {what} here only while this page is open to this computer "
                                     f"alone. {how} the .env file in the data folder instead.")
        raise HTTPException(403, f"secrets are set from the UI only while winstond listens on this machine alone "
                                 f"(control.host is {exposed[0]}); edit data/.env instead")
    if not request.client or not loopback(request.client.host):
        raise HTTPException(403, "secrets can only be changed from this machine")
    host = urlsplit("//" + request.headers.get("host", "")).hostname or ""
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, "open the UI as localhost or 127.0.0.1 to change secrets")
    if origin := request.headers.get("origin"):
        # Same origin only: a page on another localhost port is another site too.
        o, h = urlsplit(origin), urlsplit(f"{request.url.scheme}://" + request.headers.get("host", ""))
        if (o.hostname or "") not in LOCAL_HOSTS or (o.scheme, o.hostname, _port(o)) != (h.scheme, h.hostname, _port(h)):
            raise HTTPException(403, "request from another site")
    if body and not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "send JSON")


def write(name: str, value: str):
    f = env_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists():
        f.touch(mode=0o600)
    set_key(f, name, value, quote_mode="always")
    f.chmod(0o600)
    os.environ[name] = value
    logger.info(f"secret {name} set from the API")


def remove(name: str):
    f = env_file()
    if f.is_file() and name in dotenv_values(f):
        unset_key(f, name, quote_mode="always")
        f.chmod(0o600)
    os.environ.pop(name, None)
    logger.info(f"secret {name} removed from the API")
