"""Finding the Extension an extension's code makes: a single file, a folder, or `module:attr` from a
package's entry point."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from winston_sdk import extension as extension_mod
from winston_sdk.extension import Extension


class LoadError(RuntimeError):
    pass


def _import_file(path: Path, name: str, package_dir: Path | None = None):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(package_dir)] if package_dir else None)
    if spec is None or spec.loader is None:
        raise LoadError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load(target: str) -> Extension:
    """The one Extension that `target` makes."""
    before = len(extension_mod._made)
    path = Path(target)
    if path.exists():
        path = path.resolve()
        if path.is_file():
            _import_file(path, f"winston_ext_{path.stem}")
        else:
            init = path / "__init__.py"
            if init.is_file():
                _import_file(init, f"winston_ext_{path.name}", package_dir=path)
            else:
                candidates = [path / f"{path.name}.py", path / "extension.py"]
                found = next((c for c in candidates if c.is_file()), None)
                if found is None:
                    raise LoadError(f"{path}: no __init__.py, {path.name}.py or extension.py")
                _import_file(found, f"winston_ext_{path.name}")
    elif ":" in target:
        module_name, _, attr = target.partition(":")
        module = importlib.import_module(module_name)
        obj = getattr(module, attr, None)
        if not isinstance(obj, Extension):
            raise LoadError(f"{target} is not an Extension")
        return obj
    else:
        raise LoadError(f"no such file, folder or module:attr: {target}")
    made = extension_mod._made[before:]
    if len(made) != 1:
        raise LoadError(f"{target} makes {len(made)} Extension objects; it should make one")
    return made[0]
