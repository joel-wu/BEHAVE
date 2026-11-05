"""Byte-frozen runtime snapshots shared by production and validation jobs.

Fresh production snapshots contain code and launch inputs, not test suites.
Validation supplies its own file/tree selection. Continuations and local staging
always use the recorded inventory, including historical validation snapshots.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

from behave.runtime.records import write_json

TREES = ("src/behave",)
FILES = ("pyproject.toml", "uv.lock", "tools/infra_validation_job.py", "tools/infra_job_worker.py", "tools/model_service_job.py",
         "tools/dataset_validation_job.py",
         "tools/model_task_smoke.py",
         "tools/ppa_campaign.py",
         "tools/model_weight_input.py",
         "tools/model_publication_smoke.py",
         "tools/eval_review_trial.py", "tools/eval_local_generation.py", "tools/eval_score_checkpoint.py",
         "tools/eval_review_retry.py", "tools/eval_campaign.py",
         "tools/slime_raw_plan.py",
         "tools/eval_gpu_campaign.py", "tools/slime_training_plan.py", "tools/slime_training_job.py",
         "tools/slime_pause_after_checkpoint.py", "tools/slime_stop_owned.py",
         "tools/slime_checkpoint_input.py",
         "tools/setup_pipeline_sources.py", "tools/training_runtime.py",
         "tools/dataset_splits.py", "data/splits/current.json",
         "infra/train/qwen27b_acceptance.json",
         "configs/train/qwen27b_rl.example.json", "infra/train/machine.example.json",
         "infra/train/native_runtime.json", "external/manifest.toml",
         "external/patches/sglang/0001-training-runtime.patch",
         "external/patches/megatron-lm/0001-training-runtime.patch",
         "infra/model_runtimes/local-serving.json",
         "infra/model_runtimes/serving-smoke.json")


def _recorded_trees(manifest):
    # Legacy receipts have the same file inventory but no layout field. Infer
    # the owned trees from that inventory, never today's validation recipe.
    return tuple(name for name in ("src/behave", "tests/infrastructure")
                 if any(path.startswith(name + "/") for path in manifest))


def _mount_external(source: Path, target: Path) -> None:
    external = target / "external"
    if not external.exists():
        # Historical snapshots stored no files under external/.
        external.symlink_to((source / "external").resolve(), target_is_directory=True)
    else:
        # Pins and patches are frozen bytes, not links into the live checkout.
        # Only the explicitly external EDA installation stays mounted.
        (external / "oss-cad-suite").symlink_to(
            (source / "external/oss-cad-suite").resolve(), target_is_directory=True)


def _contents(repo: Path, extra_files: tuple[str, ...] = (), *, files=None, trees=TREES) -> dict[str, bytes]:
    names = (*(FILES if files is None else files), *extra_files)
    for name in names:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("snapshot additions must be repository-relative files")
    paths = [repo / name for name in names]
    # Freeze the same holdout membership used by the repository training entry.
    split_index = json.loads((repo / "data/splits/current.json").read_text())
    split = repo / "data/splits" / split_index["authored"]
    paths.extend(split / name for name in ("eval.txt", "external_eval.txt"))
    for name in trees:
        if not (repo / name).is_dir() or (repo / name).is_symlink():
            raise ValueError(f"snapshot tree is not a regular directory: {name}")
        for path in (repo / name).rglob("*"):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.is_symlink():
                raise ValueError(f"snapshot input is a symlink: {path}")
            if not path.is_dir():
                paths.append(path)
    result = {}
    for path in sorted(paths):
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError(f"snapshot input is not regular: {path}")
        result[path.relative_to(repo).as_posix()] = path.read_bytes()
    return result


def freeze(repo: Path, target: Path, *, extra_files: tuple[str, ...] = (), overrides=None,
           files=None, trees=TREES) -> dict:
    """Copy one closed file set, then reject a changing source tree."""
    # A continuation inherits the parent's recorded file set, not today's
    # launcher list, which may include scripts that did not exist at that time.
    prior_path = repo.parent / "snapshot.json"
    prior = json.loads(prior_path.read_text()) if prior_path.is_file() else None
    names = files if prior is None else tuple(prior["files"])
    trees = trees if prior is None else _recorded_trees(prior["files"])
    source = _contents(repo, extra_files, files=names, trees=trees)
    if prior is not None:
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in source.items()}
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        if (prior.get("schema") != "behave.infra-validation-snapshot/1"
                or manifest != prior["files"] or hashlib.sha256(encoded).hexdigest() != prior["sha256"]):
            raise RuntimeError("parent snapshot differs from its recorded inventory")
    before = dict(source)
    for name, data in (overrides or {}).items():
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("snapshot overrides must be repository-relative files")
        before[name] = data
    target.mkdir(parents=True, exist_ok=False)
    for name, data in before.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
    if (_contents(repo, extra_files, files=names, trees=trees) != source
            or _contents(target, extra_files, files=tuple(before), trees=trees) != before):
        raise RuntimeError("source changed during validation snapshot; no job submitted")
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in before.items()}
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    # The EDA root is explicit. It is not a copied/pinned toolchain claim.
    _mount_external(repo, target)
    for name in before:
        (target / name).chmod(0o444)
    record = {"schema": "behave.infra-validation-snapshot/1", "source": str(repo),
              "sha256": hashlib.sha256(encoded).hexdigest(), "files": manifest, "extra_files": list(extra_files),
              "interpreter": sys.executable, "eda_root": str(repo / "external/oss-cad-suite")}
    if overrides:
        record["overrides"] = sorted(overrides)
    write_json(target.parent / "snapshot.json", record)
    return record


def read_snapshot(snapshot: Path) -> tuple[dict, dict[str, bytes]]:
    record = json.loads((snapshot.parent / "snapshot.json").read_text())
    if record.get("schema") != "behave.infra-validation-snapshot/1":
        raise RuntimeError("unsupported snapshot schema")
    manifest = record["files"]
    extras = tuple(record.get("extra_files", ()))
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != record["sha256"]:
        raise RuntimeError("snapshot manifest identity mismatch")
    contents = _contents(snapshot, extras, files=tuple(manifest), trees=_recorded_trees(manifest))
    if {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()} != manifest:
        raise RuntimeError("validation snapshot differs from its frozen inventory")
    return record, contents


def verify(snapshot: Path) -> dict:
    """Validate an already staged checkout without making another copy."""
    return read_snapshot(snapshot)[0]


def stage(snapshot: Path, local: Path) -> dict:
    """Verify a durable snapshot before importing its node-local copy."""
    record, contents = read_snapshot(snapshot)
    manifest = record['files']
    local.mkdir()
    for name, data in contents.items():
        path = local / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        path.chmod(0o444)
    if {name: hashlib.sha256(data).hexdigest()
            for name, data in _contents(local, tuple(record.get('extra_files', ())),
                                       files=tuple(manifest), trees=_recorded_trees(manifest)).items()} != manifest:
        raise RuntimeError("local validation sources differ from the frozen snapshot")
    _mount_external(snapshot, local)
    return record


_LOCAL_IMPORTS = ("import sys; from pathlib import Path; source=Path(sys.argv.pop(1)); "
                  "sys.path[:0]=[str(source),str(source/'src'),str(source/'tools')]; ")


def source_command(source: Path, program: str, *arguments) -> list[str]:
    """Execute a trusted entry in a fresh interpreter rooted at selected source.

    The caller owns staging/verification. Changing sys.path in a running worker
    is insufficient: already imported packages retain their original __path__.
    The entry receives ``source`` (Path) and its arguments in sys.argv[1:].
    """
    return [sys.executable, "-I", "-B", "-u", "-c", _LOCAL_IMPORTS + program,
            str(source), *map(str, arguments)]


_STAGED_ENTRY = '''import os, subprocess, sys
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

program, prefix, temporary = sys.argv.pop(1), sys.argv.pop(1), sys.argv.pop(1)
snapshot = Path(sys.argv.pop(1))
sys.path[:0] = [str(snapshot), str(snapshot / 'src'), str(snapshot / 'tools')]
if (snapshot / 'src/behave/runtime/snapshots.py').is_file():
    from behave.runtime.snapshots import stage
else:
    # Historical snapshots predate the shared runtime. Use their own stager,
    # without overlaying today's solver/provider code onto that experiment.
    from infra_validation_job import stage

def execute(scratch):
    local = Path(scratch) / 'checkout'
    stage(snapshot, local)
    command = [sys.executable, '-I', '-B', '-u',
               *(['-S'] if sys.flags.no_site else []), '-c', program,
               str(local), *sys.argv[1:]]
    if temporary == '1':
        return subprocess.run(command, check=False).returncode
    os.execv(sys.executable, command)

if temporary == '1':
    with TemporaryDirectory(prefix=prefix, dir='/tmp') as scratch:
        code = execute(scratch)
    raise SystemExit(code)
execute(mkdtemp(prefix=prefix, dir='/tmp'))
'''


def staged_command(snapshot: Path, program: str, *arguments,
                   prefix: str = "behave-code-", temporary: bool = False) -> list[str]:
    """Build a node command; verify/stage there, then start a clean interpreter.

    No filesystem access or dispatch occurs here. Retained staging preserves
    existing job scratch; temporary staging waits for the worker before cleanup.
    """
    return [sys.executable, "-I", "-B", "-u", "-c", _STAGED_ENTRY,
            _LOCAL_IMPORTS + program, prefix, "1" if temporary else "0",
            str(snapshot), *map(str, arguments)]


def install_runtime(local: Path, report_directory: Path, native_python: str = "", *,
                    native_directory: Path | None = None, control_extras: tuple[str, ...] = ()) -> dict:
    """Install one snapshot wheel in control/native overlays, leaving the GPU ABI read-only.

    Shared by tests and production jobs. The dependency recipe is unchanged: 
    locked, offline eda+dev control environment and an explicitly selected native
    stack. Removing development dependencies is a separate reviewed change.
    """
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("runtime installation requires uv")
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTEST_ADDOPTS"):
        env.pop(name, None)
    env.update(UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never", PYTHONDONTWRITEBYTECODE="1")
    root = local.parent
    wheels, venv, requirements = root / "wheels", root / "installed", root / "requirements.txt"

    def command(args):
        print(json.dumps({"phase": "installed-runtime", "command": str(args[1])}), flush=True)
        with (report_directory / "installation.log").open("ab") as log:
            # The allocated JobRequest owns build, installation and tests as
            # one lifetime. Child stages do not mint new or shorter budgets.
            subprocess.run(tuple(map(str, args)), cwd=local, env=env, stdout=log, stderr=log,
                           check=True)

    command((uv, "export", "--locked", "--offline", "--no-emit-project", "--extra", "eda", "--extra", "dev",
             *(argument for extra in control_extras for argument in ("--extra", extra)),
             "--output-file", requirements))
    command((uv, "build", "--offline", "--wheel", "--out-dir", wheels, local))
    (wheel,) = wheels.glob("behave-*.whl")
    command((uv, "venv", "--offline", "--python", sys.executable, venv))
    python = venv / "bin/python"
    command((uv, "pip", "install", "--offline", "--python", python, "--require-hashes", "-r", requirements))
    command((uv, "pip", "install", "--offline", "--python", python, "--no-deps", wheel))
    native_base = native_python
    if native_python:
        # Reuse the pinned native stack, not the control interpreter's compiled
        # site-packages. Install only this wheel in a disposable overlay; never
        # mutate the shared native prefix or solve a second GPU dependency set.
        native_venv = native_directory if native_directory is not None else root / "native-installed"
        command((uv, "venv", "--offline", "--system-site-packages", "--python", native_python, native_venv))
        native_python = str(native_venv / "bin/python")
        command((uv, "pip", "install", "--offline", "--python", native_python, "--no-deps", wheel))
    evidence = {"schema": "behave.installed-runtime/1", "wheel": wheel.name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256((local / "uv.lock").read_bytes()).hexdigest(),
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "control_python": str(python), "native_python": native_python or None,
        "native_base_python": native_base or None,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "clean_git_release": False}
    # Keep the exact tested artifact, not only a path into deleted /tmp scratch.
    shutil.copyfile(wheel, report_directory / wheel.name)
    shutil.copyfile(requirements, report_directory / "requirements.txt")
    (report_directory / "installed.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return {"evidence": evidence, "environment": env}


def _source(component, root, *, require_hashes=False):
    manifest = tomllib.loads((root / "external/manifest.toml").read_text())[component]
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["commit"]):
        raise ValueError("external source requires a full pinned commit")
    patches = []
    hashes = manifest.get("patch_sha256", {})
    if (hashes or require_hashes) and set(hashes) != set(manifest["patches"]):
        raise ValueError("patch hashes must cover the entire series")
    for name in manifest["patches"]:
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("patch name must be a filename")
        path = root / "external/patches" / component / name
        content = path.read_bytes()
        if hashes and hashlib.sha256(content).hexdigest() != hashes[name]:
            raise ValueError(f"patch hash mismatch: {name}")
        patches.append((name, content))
    return manifest, patches


def _git(checkout, *args, check=True, env=None):
    clean = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        clean.pop(key, None)
    clean.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    clean.update(env or {})
    return subprocess.run(("git", "-C", str(checkout), *args), check=check,
                          capture_output=True, text=True, env=clean)


@contextmanager
def _expected_index(checkout, patches):
    # Apply to private objects as well as a private index: verification must not
    # leave new objects in an operator's (possibly live) checkout.
    with tempfile.TemporaryDirectory(prefix="behave-source-check-") as scratch:
        scratch = Path(scratch)
        objects = scratch / "objects"
        objects.mkdir()
        original = _git(checkout, "rev-parse", "--git-path", "objects").stdout.strip()
        env = {"GIT_INDEX_FILE": str(scratch / "index"), "GIT_OBJECT_DIRECTORY": str(objects),
               "GIT_ALTERNATE_OBJECT_DIRECTORIES": str((checkout / original).resolve())}
        _git(checkout, "read-tree", "HEAD", env=env)
        paths = []
        for index, (_, content) in enumerate(patches):
            path = scratch / f"{index}.patch"
            path.write_bytes(content)
            paths.append(path)
            _git(checkout, "apply", "--cached", str(path), env=env)
        yield env, paths


def _matches(checkout, env, changed=None):
    args = ("--", *changed) if changed is not None else ()
    if changed == []:
        return True
    result = _git(checkout, "diff", "--no-ext-diff", "--no-textconv", "--quiet", *args,
                  env=env, check=False)
    if result.returncode not in (0, 1):
        result.check_returncode()
    if result.returncode:
        return False
    return changed is not None or not _git(checkout, "ls-files", "--others", "--exclude-standard",
                                          env=env).stdout.strip()


def install(name, *, root, checkout=None, exact=False, verify_only=False):
    """Reuse the patch installer; exact/verify modes are for native source checks.

    Exact installation accepts a clean base or the complete expected tree, never
    repairs an unknown dirty checkout. Default method-adapter behavior still
    allows unrelated local work and previously applied patch prefixes.
    """
    root = Path(root)
    manifest, patches = _source(name, root, require_hashes=exact)
    checkout = Path(checkout) if checkout is not None else root / "external" / name
    checkout = checkout.absolute()
    if checkout.is_symlink():
        raise ValueError("external checkout must not be a symlink")
    if not checkout.exists():
        if verify_only:
            raise ValueError(f"source checkout is absent: {checkout}")
        subprocess.run(("git", "clone", "--no-hardlinks", manifest["upstream"], str(checkout)), check=True)
        _git(checkout, "checkout", "--detach", manifest["commit"])
    top = _git(checkout, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve() != checkout.resolve():
        raise ValueError("source path must be a checkout root")
    if _git(checkout, "rev-parse", "HEAD").stdout.strip() != manifest["commit"]:
        raise RuntimeError(f"{checkout} is not at the pinned commit; keep local work and resolve it explicitly")
    with _expected_index(checkout, patches) as (env, paths):
        changed = None if exact else _git(checkout, "diff", "--cached", "--name-only",
                                          env=env).stdout.splitlines()
        complete = _matches(checkout, env, changed)
        if not complete and verify_only:
            raise RuntimeError(f"{name}: source does not match the pinned commit plus patches")
        if not complete:
            if exact:
                if _git(checkout, "diff", "--cached", "--quiet", "HEAD", check=False).returncode:
                    raise RuntimeError(f"{name}: refusing to change a checkout with staged edits")
                with _expected_index(checkout, []) as (base, _):
                    if not _matches(checkout, base):
                        raise RuntimeError(f"{name}: refusing to change a dirty or partially patched checkout")
            for path in paths:
                if _git(checkout, "apply", "--reverse", "--check", str(path), check=False).returncode == 0:
                    continue
                _git(checkout, "apply", "--check", str(path))
                _git(checkout, "apply", str(path))
            if not _matches(checkout, env, changed):
                raise RuntimeError(f"{name}: patched source failed verification")
    return {"method": name, "commit": manifest["commit"], "patches": manifest["patches"]}
