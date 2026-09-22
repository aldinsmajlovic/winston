"""Plugin registry. Adapters are found through entry points (built-ins included), so a
third-party package adds one by declaring `[project.entry-points."winston.<kind>"]`."""
from importlib import import_module
from importlib.metadata import entry_points

KINDS = ("stt", "tts", "llm", "wake", "channel", "tool")


def available(kind: str) -> dict[str, str]:
    """name -> 'module:attr' for every registered adapter of a kind."""
    return {ep.name: ep.value for ep in entry_points(group=f"winston.{kind}")}


def get(kind: str, name: str):
    eps = {ep.name: ep for ep in entry_points(group=f"winston.{kind}")}
    if name not in eps:
        raise KeyError(f"no {kind} adapter named {name!r}; available: {sorted(eps)}")
    ep = eps[name]
    module, _, attr = ep.value.partition(":")
    return getattr(import_module(module), attr)
