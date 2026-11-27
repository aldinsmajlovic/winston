"""Whether the brain answers, kept the way the out-of-memory note is (config/memory.py), so every process that
calls the brain can say so: a failed call writes its reason to data/state/brain_failure.txt, which the
supervisor shows as the Brain's status, and a line to data/logs/brain.log, which a hosted brain's Log shows.
The first call that works after one deletes the note. A hosted brain counts as up whenever winstond runs, so
without this a refused key read as Running."""
import time
from pathlib import Path

from winston.config import data_dir

# Fixed words for what the person can act on. The provider's own text is left out: a refused key's may echo it.
WORDS = {401: "Key refused", 403: "Key refused", 402: "Out of credit", 404: "No such model, or a wrong API address",
         429: "Too many requests"}


def _note() -> Path:
    return data_dir() / "state" / "brain_failure.txt"


def _log() -> Path:
    return data_dir() / "logs" / "brain.log"


def why_failed(e: BaseException) -> str:
    """A failed brain call in words: what the provider's answer means, or that it could not be reached."""
    from winston.core.brain import unreachable
    code = getattr(e, "status_code", None)
    if code in WORDS:
        return f"{WORDS[code]} ({code})"
    if code is not None:
        return f"Refused ({code}): {str(e)[:200]}"
    if unreachable(e):
        return "Not reachable"
    return str(e)[:200] or type(e).__name__


def log(channel: str, text: str) -> None:
    _log().parent.mkdir(parents=True, exist_ok=True)
    with _log().open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {channel}  {text}\n")


def failed(channel: str, e: BaseException) -> str:
    """A brain call from channel failed: noted for the status and logged. Returns the words."""
    why = why_failed(e)
    _note().parent.mkdir(parents=True, exist_ok=True)
    _note().write_text(why)
    log(channel, why)
    return why


def worked(channel: str, say: str = "") -> None:
    """A brain call from channel worked: any failure no longer holds. Logs say, or that it works again."""
    had = _note().exists()
    _note().unlink(missing_ok=True)
    if say or had:
        log(channel, say or "works again")


def failure() -> str:
    """Why the brain's last call failed, until one works, it restarts or its settings change; "" if not."""
    try:
        return _note().read_text()
    except OSError:
        return ""


def clear() -> None:
    _note().unlink(missing_ok=True)


def log_tail(n: int) -> list[str]:
    try:
        return _log().read_text().splitlines()[-n:]
    except OSError:
        return []
