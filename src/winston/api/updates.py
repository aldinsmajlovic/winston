"""Updates (docs/setup.md#updating): the latest release, checked when winstond starts, every six hours and
when the page asks after 15 minutes, with what changed since this one from its CHANGELOG.md; and an update
from the page, which installs the release beside this one as `winston update` does and restarts the service.
In a clone the check still runs, but updating is git's."""
import asyncio
import subprocess
import time
from dataclasses import dataclass, field

from loguru import logger

from winston import __version__, setup
from winston.config import repo_root

EVERY = 6 * 3600
STALE = 15 * 60


@dataclass
class State:
    latest: str | None = None
    notes: list[dict] = field(default_factory=list)     # the releases newer than this one, newest first
    checked: float | None = None
    error: str = ""
    stage: str = ""             # "" | installing | restarting | installed | error
    detail: str = ""            # the step under way, the version installed, or why it stopped


STATE = State()
_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()


def version() -> str:
    """This one: the release's, or in a clone what git says ("0.1.2-3-gabc1234"), since a clone's pyproject
    keeps the version it was started with and releases stamp theirs."""
    if setup.release_app():
        return __version__
    r = subprocess.run(["git", "-C", str(repo_root()), "describe", "--tags", "--dirty"],
                       capture_output=True, text=True, check=False)
    return r.stdout.strip().removeprefix("v") or __version__


async def check(force: bool = False) -> None:
    async with _lock:
        if not force and STATE.checked and time.time() - STATE.checked < STALE:
            return
        try:
            STATE.latest, releases = await asyncio.to_thread(setup.latest)
            # A clone's __version__ is not its release, so it gets no list of what it lacks.
            STATE.notes, STATE.error = setup.after(releases, __version__) if setup.release_app() else [], ""
        except Exception as e:      # noqa: BLE001  offline or GitHub down: said on the page, tried again later
            STATE.error = "Could not check for a new release; it tries again in a while."
            logger.info(f"updates: the latest release not read: {type(e).__name__}: {e}")
        STATE.checked = time.time()


async def loop() -> None:
    while True:
        await check(force=True)
        await asyncio.sleep(EVERY)


def start(to: str | None) -> None:
    if STATE.stage in ("installing", "restarting"):
        raise setup.UpdateError("An update is under way.")
    if not setup.release_app():
        raise setup.UpdateError(setup.CLONE)
    STATE.stage, STATE.detail = "installing", "Starting"
    t = asyncio.create_task(_run(to))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _run(to: str | None) -> None:
    def say(step: str) -> None:
        STATE.detail = step
    try:
        new = await asyncio.to_thread(setup.install, to, say)
    except setup.UpdateError as e:
        STATE.stage, STATE.detail = "error", str(e)
        return
    except Exception as e:      # noqa: BLE001  a full disk, a folder it may not write: the page says so
        logger.exception("updates: the update stopped")
        STATE.stage, STATE.detail = "error", f"The update stopped: {type(e).__name__}: {e}"
        return
    if not new:
        STATE.stage, STATE.detail = "", ""
        return
    logger.info(f"updates: Winston {new} installed beside {__version__}")
    if not await asyncio.to_thread(setup.service_running):
        STATE.stage, STATE.detail = "installed", new
        return
    STATE.stage, STATE.detail = "restarting", new
    await asyncio.sleep(1)          # the page's next poll sees it before the restart ends this process
    if err := await asyncio.to_thread(setup.restart_service, False):
        STATE.stage, STATE.detail = "installed", new
        logger.warning(f"updates: the service did not restart: {err}")
