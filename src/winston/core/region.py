"""Region: the units and formats Winston speaks in (temperature, distance, time, date, money).
Each setting in config's `region` is either explicit or "auto", which follows Home Assistant's own
configuration (its unit system, country and currency), read once per process. The result is a
short block at the end of the system prompt, read when a conversation starts like the memory."""
import os
from functools import lru_cache

import httpx
from loguru import logger

from winston.config.schema import WinstonConfig

TWELVE_HOUR = {"US", "CA", "AU", "NZ", "IN", "PH", "PK", "EG", "SA"}     # countries that mostly use a 12-hour clock


@lru_cache(maxsize=1)
def _home_assistant(url: str, token_env: str) -> dict:
    token = os.environ.get(token_env, "")
    if not token:
        return {}
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/config", headers={"Authorization": f"Bearer {token}"}, timeout=3)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        logger.warning(f"region: Home Assistant's config not readable ({type(e).__name__}); using metric and 24-hour")
        return {}


def resolve(cfg: WinstonConfig) -> dict[str, str]:
    """Every region setting with "auto" replaced by what Home Assistant says."""
    r, ha = cfg.region, cfg.providers.ha
    info = _home_assistant(ha.url, ha.token_env) if ha else {}
    units, country = info.get("unit_system") or {}, (info.get("country") or "").upper()
    auto = {
        "temperature": "fahrenheit" if units.get("temperature") == "°F" else "celsius",
        "distance": "imperial" if units.get("length") == "mi" else "metric",
        "time_format": "12h" if country in TWELVE_HOUR else "24h",
        "date_format": "month_day" if country == "US" else "day_month",
        "currency": info.get("currency") or "",
    }
    chosen = {"temperature": r.temperature, "distance": r.distance, "time_format": r.time_format,
              "date_format": r.date_format, "currency": r.currency or "auto"}
    return {k: auto[k] if v == "auto" else v for k, v in chosen.items()}


def prompt_block(cfg: WinstonConfig) -> str:
    v = resolve(cfg)
    parts = [
        "temperatures in degrees " + ("Fahrenheit" if v["temperature"] == "fahrenheit" else "Celsius"),
        "distances, weights and volumes in " + ("imperial units (miles, pounds, gallons)" if v["distance"] == "imperial"
                                                else "metric units (kilometres, kilograms, litres)"),
        "times on the " + ("12-hour clock (\"half past five in the afternoon\")" if v["time_format"] == "12h"
                           else "24-hour clock (\"seventeen thirty\")"),
        "dates " + ("month first (\"September 24\")" if v["date_format"] == "month_day" else "day first (\"24 September\")"),
    ]
    money = (f" The home's currency is {v['currency']}; say prices in another currency as they are, never with a "
             f"guessed exchange rate." if v["currency"] else "")
    return ("# Region\n\nSpeak " + ", ".join(parts) + ". Convert units and formats a tool or a web search gives "
            "otherwise before you say them; mention the original only when asked." + money)
