"""Native source preparation and read-only checks without a GPU or network."""

import hashlib
import json
import platform
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from behave.runtime.snapshots import install
from behave.train import runtime_setup as training_runtime


def git(directory, *args):
    return subprocess.run(("git", "-C", str(directory), *args), check=True,
                          capture_output=True, text=True).stdout.strip()


def source_workspace(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "--quiet")
    git(upstream, "config", "user.email", "fixture@example.invalid")
    git(upstream, "config", "user.name", "Fixture")
    (upstream / "module.py").write_text("value = 1\n")
    (upstream / ".gitignore").write_text("__pycache__/\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "base")
    commit = git(upstream, "rev-parse", "HEAD")
    (upstream / "module.py").write_text("value = 2\n")
    patch = git(upstream, "diff", "--full-index") + "\n"
    (upstream / "module.py").write_text("value = 1\n")
    patch += ("diff --git a/added.py b/added.py\nnew file mode 100644\n"
              "--- /dev/null\n+++ b/added.py\n@@ -0,0 +1 @@\n+extra = True\n")
    root = tmp_path / "release"
    for component in training_runtime.COMPONENTS:
        path = root / "external/patches" / component / "native.patch"
        path.parent.mkdir(parents=True)
        path.write_text(patch)
    digest = hashlib.sha256(patch.encode()).hexdigest()
    manifest = "\n".join(f'[{name}]\nupstream = {json.dumps(str(upstream))}\n'
                         f'commit = "{commit}"\npatches = ["native.patch"]\n'
                         f'patch_sha256 = {{"native.patch" = "{digest}"}}\n'
                         for name in training_runtime.COMPONENTS)
    (root / "external/manifest.toml").write_text(manifest)
    return root, upstream, tmp_path / "installed-sources"


@pytest.fixture
def sources(tmp_path):
    return source_workspace(tmp_path)


@pytest.mark.parametrize("mode", ["sources", "check"])
def test_installed_cli_requires_explicit_repository(tmp_path, monkeypatch, mode):
    from behave.cli.training_runtime import main

    monkeypatch.setattr(training_runtime, "prepare_sources", lambda *a, **kw: pytest.fail("must not prepare"))
    monkeypatch.setattr(training_runtime, "check", lambda *a, **kw: pytest.fail("must not inspect"))
    arguments = [mode, "--directory", str(tmp_path)] if mode == "sources" else [mode, "--configuration", "config.json"]
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2


def test_prepare_sources_and_repeat_preserve_index_and_objects(sources):
    root, _, target = sources
    result = training_runtime.prepare_sources(target, root=root)
    assert result["status"] == "prepared" and result["packages_installed"] is False
    checkout = target / "sglang"
    assert (checkout / "module.py").read_text() == "value = 2\n"
    assert (checkout / "added.py").read_text() == "extra = True\n"
    index = (checkout / ".git/index").read_bytes()
    objects = set((checkout / ".git/objects").rglob("*"))
    assert training_runtime.prepare_sources(target, root=root) == result
    install("sglang", root=root, checkout=checkout, exact=True, verify_only=True)
    assert (checkout / ".git/index").read_bytes() == index
    assert set((checkout / ".git/objects").rglob("*")) == objects


@pytest.mark.parametrize("kind", ["tracked", "untracked", "staged", "missing-patch-file"])
def test_unknown_changes_are_not_repaired_or_overwritten(sources, kind):
    root, _, target = sources
    checkout = target / "sglang"
    install("sglang", root=root, checkout=checkout, exact=True)
    if kind == "missing-patch-file":
        (checkout / "added.py").unlink()
    else:
        edited = checkout / ("other.py" if kind == "untracked" else "module.py")
        edited.write_text("user_change = True\n")
        if kind == "staged":
            git(checkout, "add", "module.py")
    index = (checkout / ".git/index").read_bytes()
    for verify in (True, False):
        with pytest.raises(RuntimeError):
            install("sglang", root=root, checkout=checkout, exact=True, verify_only=verify)
    assert (checkout / ".git/index").read_bytes() == index
    if kind == "missing-patch-file":
        assert not (checkout / "added.py").exists()
    else:
        assert edited.read_text() == "user_change = True\n"


def test_wrong_commit_is_not_checked_out_over_local_work(sources):
    root, upstream, target = sources
    checkout = target / "slime"
    install("slime", root=root, checkout=checkout, exact=True)
    path = root / "external/manifest.toml"
    path.write_text(path.read_text().replace(git(upstream, "rev-parse", "HEAD"), "f" * 40))
    before = git(checkout, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="pinned commit"):
        install("slime", root=root, checkout=checkout, exact=True)
    assert git(checkout, "rev-parse", "HEAD") == before


def test_all_patch_hashes_checked_before_any_clone(sources):
    root, _, target = sources
    (root / "external/patches/apex/native.patch").write_text("damaged")
    with pytest.raises(ValueError, match="hash mismatch"):
        training_runtime.prepare_sources(target, root=root)
    assert not target.exists()


def test_native_patch_hashes_cannot_be_omitted(sources):
    root, _, target = sources
    manifest = root / "external/manifest.toml"
    manifest.write_text("\n".join(line for line in manifest.read_text().splitlines()
                                  if not line.startswith("patch_sha256")))
    with pytest.raises(ValueError, match="entire series"):
        training_runtime.prepare_sources(target, root=root)
    assert not target.exists()


def test_verify_missing_source_never_downloads(sources):
    root, _, target = sources
    with pytest.raises(ValueError, match="absent"):
        install("slime", root=root, checkout=target / "slime", exact=True, verify_only=True)
    assert not target.exists()


def test_explicit_source_directory_and_patch_paths_are_required(sources):
    root, _, target = sources
    for path in (Path("relative"), Path("/"), target / ".." / "elsewhere"):
        with pytest.raises(ValueError, match="absolute"):
            training_runtime.prepare_sources(path, root=root)
    manifest = root / "external/manifest.toml"
    manifest.write_text(manifest.read_text().replace("native.patch", "../escape.patch"))
    with pytest.raises(ValueError, match="filename"):
        training_runtime.prepare_sources(target, root=root)
    assert not target.exists()


def test_preexisting_file_conflicting_with_patch_is_not_overwritten(sources):
    root, upstream, target = sources
    checkout = target / "sglang"
    target.mkdir()
    subprocess.run(("git", "clone", str(upstream), str(checkout)), check=True, capture_output=True)
    (checkout / "added.py").write_text("valuable user file\n")
    with pytest.raises(RuntimeError, match="refusing"):
        install("sglang", root=root, checkout=checkout, exact=True)
    assert (checkout / "added.py").read_text() == "valuable user file\n"
    assert (checkout / "module.py").read_text() == "value = 1\n"


def test_staged_edits_are_preserved_even_if_working_file_matches_base(sources):
    root, upstream, target = sources
    checkout = target / "slime"
    target.mkdir()
    subprocess.run(("git", "clone", str(upstream), str(checkout)), check=True, capture_output=True)
    (checkout / "module.py").write_text("staged = True\n")
    git(checkout, "add", "module.py")
    (checkout / "module.py").write_text("value = 1\n")
    before = (checkout / ".git/index").read_bytes()
    with pytest.raises(RuntimeError, match="staged edits"):
        install("slime", root=root, checkout=checkout, exact=True)
    assert (checkout / ".git/index").read_bytes() == before
    assert (checkout / "module.py").read_text() == "value = 1\n"


def test_metadata_probe_runs_without_importing_training_modules():
    result = training_runtime.inspect_environment(sys.executable, {"pytest": "unused", "not-a-real-package": "1"})
    assert result["python"] == platform.python_version()
    assert result["packages"]["pytest"] == pytest.__version__
    assert result["packages"]["not-a-real-package"] is None


def test_probe_rejects_nonabsolute_interpreter():
    with pytest.raises(ValueError, match="absolute"):
        training_runtime.inspect_environment("python", {})


@pytest.fixture
def runtime(sources, monkeypatch):
    root, _, target = sources
    training_runtime.prepare_sources(target, root=root)
    packages = {"slime": "0.3.1", "sglang": "0.5.15.post1", "megatron-core": "0.16.0rc0"}
    expected = {"schema": "behave.native-runtime-observation/1", "python": "3.12.14", "packages": packages}
    path = root / "infra/train/native_runtime.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(expected))
    config = root / "configuration.json"
    config.write_text(json.dumps({"native_python": sys.executable, "slime_source": str(target / "slime"),
                                  "megatron_source": str(target / "megatron-lm")}))
    actual = {"python": "3.12.14", "packages": deepcopy(packages), "origins": {}}
    for name, path in (("slime", target / "slime"), ("sglang", target / "sglang/python"),
                       ("megatron-core", target / "megatron-lm")):
        actual["origins"][name] = {"editable": True, "local_path": str(path)}
    monkeypatch.setattr(training_runtime, "inspect_environment", lambda *a, **kw: actual)
    return root, target, config, actual


def test_check_records_limited_acceptance_not_gpu_or_installation_claim(runtime):
    root, _, config, _ = runtime
    result = training_runtime.check(config, root=root)
    assert result["status"] == "matched" and result["errors"] == []
    assert set(result["sources"]) == {"slime", "sglang", "megatron-lm"}
    assert result["gpu_validation"] == "not_run"
    assert result["clean_installation"] == "not_verified"


def test_unavailable_interpreter_is_a_failed_check_with_a_receipt(runtime, monkeypatch):
    root, _, config, _ = runtime

    def missing(*args, **kwargs):
        raise FileNotFoundError("interpreter unavailable")

    monkeypatch.setattr(training_runtime, "inspect_environment", missing)
    result = training_runtime.check(config, root=root)
    assert result["status"] == "mismatch" and result["actual"] is None
    assert "FileNotFoundError" in result["errors"][0]
    assert result["gpu_validation"] == "not_run"


@pytest.mark.parametrize("damage", ["version", "missing", "python", "source-path", "not-editable", "patch"])
def test_check_rejects_mismatched_runtime(runtime, damage):
    root, target, config, actual = runtime
    if damage == "version":
        actual["packages"]["sglang"] = "new-version"
    elif damage == "missing":
        actual["packages"].pop("slime")
    elif damage == "python":
        actual["python"] = "3.11.0"
    elif damage == "source-path":
        actual["origins"]["slime"]["local_path"] = str(target / "other-slime")
    elif damage == "not-editable":
        actual["origins"]["sglang"]["editable"] = False
    else:
        (target / "sglang/module.py").write_text("bad = True\n")
    result = training_runtime.check(config, root=root)
    assert result["status"] == "mismatch" and result["errors"]


def test_check_cli_writes_failure_receipt_and_returns_nonzero(tmp_path, monkeypatch):
    from behave.cli.training_runtime import main

    result = {"status": "mismatch", "errors": ["wrong source"]}
    monkeypatch.setattr(training_runtime, "check", lambda *a, **kw: result)
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(sys, "argv", ["training-runtime", "check", "--configuration", "config.json",
                                      "--repository", str(tmp_path), "--output", str(output)])
    assert main() == 1
    assert json.loads(output.read_text()) == result
    with pytest.raises(SystemExit):
        main()
    assert json.loads(output.read_text()) == result
