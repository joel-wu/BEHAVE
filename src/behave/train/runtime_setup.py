"""Prepare pinned native sources or check an explicit training environment.

No package installation, CUDA import, GPU allocation or training dispatch is
performed here. Native wheel building and kernel acceptance are separate gates.
"""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from behave.runtime.snapshots import _source, install
from behave.train.plan import load_configuration
from behave.train.slime_launch import megatron_source

COMPONENTS = ("slime", "sglang", "megatron-lm", "apex")

# Execute only standard-library metadata reads in the selected interpreter.
# Do not return credentials or arbitrary direct_url fields from installations.
METADATA_PROBE = r'''
import importlib.metadata as metadata
import json
import platform
import sys
from urllib.parse import unquote, urlsplit

packages, origins = {}, {}
for name in json.loads(sys.argv[1]):
    try:
        dist = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        packages[name] = None
        continue
    packages[name] = dist.version
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    url = urlsplit(direct.get("url", ""))
    origin = {"editable": bool(direct.get("dir_info", {}).get("editable", False))}
    if url.scheme == "file" and url.netloc in ("", "localhost"):
        origin["local_path"] = unquote(url.path)
    digest = direct.get("archive_info", {}).get("hashes", {}).get("sha256")
    if digest:
        origin["archive_sha256"] = digest
    origins[name] = origin
print(json.dumps({"python": platform.python_version(), "packages": packages, "origins": origins}))
'''


def prepare_sources(directory: Path, *, root: Path) -> dict:
    directory = Path(directory)
    if not directory.is_absolute() or directory == Path(directory.anchor) or ".." in directory.parts:
        raise ValueError("choose an absolute, dedicated source directory")
    if directory.is_symlink():
        raise ValueError("source directory must not be a symlink")
    for name in COMPONENTS:
        _source(name, root, require_hashes=True)  # Check all patches before any download.
    sources = {name: install(name, root=root, checkout=directory / name, exact=True)
               for name in COMPONENTS}
    return {"schema": "behave.native-sources/1", "scope": "native-sources-only", "status": "prepared",
            "directory": str(directory), "sources": sources, "packages_installed": False,
            "manifest_sha256": hashlib.sha256((root / "external/manifest.toml").read_bytes()).hexdigest()}


def inspect_environment(python: str, packages: dict, *, timeout: float = 120) -> dict:
    if not Path(python).is_absolute():
        raise ValueError("native_python must be an absolute interpreter path")
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="")
    result = subprocess.run((python, "-I", "-B", "-c", METADATA_PROBE, json.dumps(sorted(packages))),
                            env=env, check=True, capture_output=True, text=True, timeout=timeout)
    return json.loads(result.stdout)


def check(configuration: Path, *, machine_profile: Path | None = None, root: Path) -> dict:
    config = load_configuration(configuration, machine_profile=machine_profile)
    expected_bytes = (root / "infra/train/native_runtime.json").read_bytes()
    expected = json.loads(expected_bytes)
    if expected["schema"] != "behave.native-runtime-observation/1":
        raise ValueError("unknown native runtime observation schema")
    report = {"schema": "behave.native-runtime-check/1", "status": "mismatch",
              "scope": "metadata-and-editable-sources", "native_python": config["native_python"],
              "manifest_sha256": hashlib.sha256((root / "external/manifest.toml").read_bytes()).hexdigest(),
              "observation_sha256": hashlib.sha256(expected_bytes).hexdigest(),
              "gpu_validation": "not_run", "clean_installation": "not_verified"}
    try:
        actual = inspect_environment(config["native_python"], expected["packages"])
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return {**report, "checked_at": datetime.now(UTC).isoformat(), "actual": None, "sources": {},
                "errors": [f"native metadata inspection failed: {type(error).__name__}"],
                "probe_returncode": getattr(error, "returncode", None)}
    errors = []
    if actual["python"] != expected["python"]:
        errors.append(f"Python: expected {expected['python']}, found {actual['python']}")
    for name, version in expected["packages"].items():
        found = actual["packages"].get(name)
        if found != version:
            errors.append(f"{name}: expected {version}, found {found}")

    # Slime, SGLang and Megatron are editable in the experimental runtime.
    # Validate installation locations, tracked files and non-ignored additions,
    # not just a version string or the presence of one patched function.
    sources = {}
    configured = {"slime": Path(config["slime_source"]), "megatron-lm": megatron_source(config)}
    for component, distribution in (("slime", "slime"), ("sglang", "sglang"),
                                     ("megatron-lm", "megatron-core")):
        origin = actual["origins"].get(distribution, {})
        location = origin.get("local_path")
        if not location or not origin.get("editable"):
            errors.append(f"{distribution}: expected a locally installed editable source")
            continue
        source = Path(location)
        if not source.is_absolute():
            errors.append(f"{distribution}: installation source is not absolute")
            continue
        if component == "sglang":
            if source.name != "python":
                errors.append("sglang: expected the pinned checkout's python/ installation")
                continue
            source = source.parent
        if component in configured and source.resolve() != configured[component].resolve():
            errors.append(f"{component}: installed source differs from the configured source")
            continue
        try:
            sources[component] = install(component, root=root, checkout=source, exact=True, verify_only=True)
        except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
            errors.append(f"{component}: {error}")

    return {**report, "checked_at": datetime.now(UTC).isoformat(),
            "status": "matched" if not errors else "mismatch", "actual": actual,
            "sources": sources, "errors": errors}
