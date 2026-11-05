import hashlib
import json
import re
import zipfile
from pathlib import Path

import pytest

from tools import locked_environment
from tools.locked_environment import (
    LockValidationError,
    _artifact_project_is_exact,
    compare_versions,
    expected_versions,
    validate,
)


def test_export_uses_enclosing_deadline_and_retains_full_failure(tmp_path, monkeypatch):
    message = "export context " * 100 + "last error"
    monkeypatch.setattr(locked_environment, "_locked_project", lambda lock: ("demo", "1"))
    monkeypatch.setattr(locked_environment.shutil, "which", lambda name: "/usr/bin/uv")

    def run(command, **kwargs):
        assert "timeout" not in kwargs
        assert command[1:4] == ["export", "--locked", "--offline"]
        return locked_environment.subprocess.CompletedProcess(command, 1, "", message)

    monkeypatch.setattr(locked_environment.subprocess, "run", run)
    with pytest.raises(LockValidationError) as caught:
        validate(tmp_path / "uv.lock", tmp_path, ())
    assert str(caught.value) == "uv export failed: " + message


def test_the_uv_export_is_reduced_to_the_current_exact_versions():
    exported = """
-e .
alpha==1.2
win-only==3 ; sys_platform == 'win32'
"""
    assert expected_versions(exported, ("demo", "0.4")) == {
        "alpha": "1.2",
        "demo": "0.4",
    }


@pytest.mark.parametrize(
    ("installed", "finding"),
    [
        ({"demo": "1"}, "missing: alpha"),
        ({"demo": "1", "alpha": "2", "surprise": "1"}, "unexpected: surprise"),
        ({"demo": "1", "alpha": "2"}, "alpha=2 (locked 1)"),
    ],
)
def test_missing_extra_and_changed_distributions_are_all_refused(installed, finding):
    with pytest.raises(LockValidationError, match=re.escape(finding)):
        compare_versions({"demo": "1", "alpha": "1"}, installed)


@pytest.mark.locked_env
def test_the_active_verifier_environment_is_exactly_the_eda_dev_lock_closure():
    root = Path(__file__).resolve().parents[2]
    record = validate(root / "uv.lock", root, ("eda", "dev"))
    assert record["schema"] == "behave-locked-environment/2"
    assert record["project_install"] == {
        "mode": "editable", "path": str(root.resolve())}
    assert record["packages"]["behave"] == "0.1.0"
    assert record["packages"]["pytest"] == "9.1.1"


class _Distribution:
    def __init__(self, direct_url, root=None, files=()):
        self._direct_url = direct_url
        self._root = root
        self.files = tuple(Path(path) for path in files)

    def read_text(self, name):
        assert name == "direct_url.json"
        return json.dumps(self._direct_url)

    def locate_file(self, path):
        assert self._root is not None
        return self._root / path


def _wheel_and_install(tmp_path):
    wheel = tmp_path / "behave-0.1.0-py3-none-any.whl"
    members = {
        "behave/__init__.py": b"VALUE = 7\n",
        "behave-0.1.0.dist-info/METADATA": b"Name: behave\nVersion: 0.1.0\n",
        "behave-0.1.0.dist-info/RECORD": b"",
    }
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    installed = tmp_path / "site-packages"
    for name, payload in members.items():
        path = installed / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return wheel, installed, tuple(members)


def test_a_fresh_install_is_bound_to_the_exact_wheel_bytes(tmp_path):
    wheel, installed, files = _wheel_and_install(tmp_path)
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    distribution = _Distribution({
        "archive_info": {},
        "url": wheel.resolve().as_uri(),
    }, installed, files)

    assert _artifact_project_is_exact(
        wheel, "behave", (distribution,)) == {
            "mode": "artifact",
            "path": str(wheel.resolve()),
            "sha256": digest,
        }

    (installed / "behave" / "__init__.py").write_bytes(b"VALUE = 8\n")
    with pytest.raises(LockValidationError, match="installed payload differs"):
        _artifact_project_is_exact(wheel, "behave", (distribution,))


def test_an_editable_or_different_artifact_cannot_pose_as_a_fresh_install(tmp_path):
    wheel, installed, files = _wheel_and_install(tmp_path)
    other = tmp_path / "other.whl"
    other.write_bytes(b"other")

    editable = _Distribution({
        "dir_info": {"editable": True}, "url": tmp_path.resolve().as_uri()},
        installed, files)
    with pytest.raises(LockValidationError, match="is editable"):
        _artifact_project_is_exact(wheel, "behave", (editable,))

    wrong_path = _Distribution({
        "archive_info": {},
        "url": other.resolve().as_uri(),
    }, installed, files)
    with pytest.raises(LockValidationError, match="not .*behave-0.1.0"):
        _artifact_project_is_exact(wheel, "behave", (wrong_path,))
