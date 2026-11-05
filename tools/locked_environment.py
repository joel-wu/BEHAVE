"""Verify that the active Python environment is exactly a selected uv lock closure.

The lock file itself is not an installed-environment check: an editable environment
can acquire extra distributions, or one of its transitive dependencies can drift,
while ``uv.lock`` remains unchanged.  This command asks the pinned ``uv`` executable
to export the selected closure and compares every installed distribution with it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


class LockValidationError(RuntimeError):
    """The active environment is not the requested locked environment."""


SCHEMA = "behave-locked-environment/2"


def _locked_project(lock: Path) -> tuple[str, str]:
    try:
        document = tomllib.loads(lock.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LockValidationError(f"cannot read lock file {lock}: {exc}") from exc
    projects = [
        row for row in document.get("package", ())
        if isinstance(row, dict)
        and row.get("source") == {"editable": "."}
    ]
    if len(projects) != 1:
        raise LockValidationError(
            "uv.lock must contain exactly one project with source = { editable = '.' }")
    project = projects[0]
    name, version = project.get("name"), project.get("version")
    if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
        raise LockValidationError("the editable project in uv.lock has no name or version")
    return canonicalize_name(name), version


def expected_versions(exported: str, project: tuple[str, str]) -> dict[str, str]:
    """Parse uv's unannotated requirements export for the current interpreter."""
    expected: dict[str, str] = {}
    saw_project = False
    for raw in exported.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line == "-e .":
            if saw_project:
                raise LockValidationError("the exported closure repeats the editable project")
            saw_project = True
            name, version = project
        else:
            try:
                requirement = Requirement(line)
            except Exception as exc:  # packaging exposes several parse error subclasses
                raise LockValidationError(
                    f"uv exported an unsupported requirement: {line!r}") from exc
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            if requirement.url is not None or requirement.extras:
                raise LockValidationError(
                    f"uv exported a non-version requirement: {line!r}")
            pins = list(requirement.specifier)
            if len(pins) != 1 or pins[0].operator != "==" or pins[0].version.endswith(".*"):
                raise LockValidationError(
                    f"uv exported a requirement without one exact version: {line!r}")
            name, version = canonicalize_name(requirement.name), pins[0].version
        previous = expected.setdefault(name, version)
        if previous != version:
            raise LockValidationError(
                f"the selected lock closure has two versions of {name}: "
                f"{previous} and {version}")
    if not saw_project:
        raise LockValidationError("uv did not export the editable project")
    return expected


def installed_versions() -> tuple[dict[str, str], dict[str, tuple[importlib.metadata.Distribution, ...]]]:
    """Return canonical installed versions, rejecting conflicting metadata copies."""
    grouped: dict[str, list[importlib.metadata.Distribution]] = {}
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise LockValidationError(
                f"installed distribution at {distribution.locate_file('')} has no Name metadata")
        grouped.setdefault(canonicalize_name(raw_name), []).append(distribution)
    versions: dict[str, str] = {}
    frozen: dict[str, tuple[importlib.metadata.Distribution, ...]] = {}
    for name, rows in grouped.items():
        found = {row.version for row in rows}
        if len(found) != 1:
            raise LockValidationError(
                f"installed metadata gives multiple versions for {name}: {sorted(found)}")
        versions[name] = found.pop()
        frozen[name] = tuple(rows)
    return versions, frozen


def compare_versions(expected: dict[str, str], installed: dict[str, str]) -> None:
    missing = sorted(set(expected) - set(installed))
    extra = sorted(set(installed) - set(expected))
    changed = sorted(
        (name, expected[name], installed[name])
        for name in expected.keys() & installed.keys()
        if expected[name] != installed[name]
    )
    findings: list[str] = []
    if missing:
        findings.append("missing: " + ", ".join(missing))
    if extra:
        findings.append("unexpected: " + ", ".join(extra))
    if changed:
        findings.append(
            "wrong versions: "
            + ", ".join(f"{name}={actual} (locked {wanted})"
                        for name, wanted, actual in changed)
        )
    if findings:
        raise LockValidationError("; ".join(findings))


def _editable_project_is_this_checkout(
    project: Path,
    name: str,
    distributions: tuple[importlib.metadata.Distribution, ...],
) -> None:
    wanted = project.resolve()
    locations: list[Path] = []
    for distribution in distributions:
        raw = distribution.read_text("direct_url.json")
        if raw is None:
            continue
        try:
            direct = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LockValidationError(f"{name} has malformed direct_url.json") from exc
        if direct.get("dir_info", {}).get("editable") is not True:
            continue
        parsed = urlparse(direct.get("url", ""))
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            continue
        locations.append(Path(unquote(parsed.path)).resolve())
    if wanted not in locations:
        rendered = ", ".join(map(str, locations)) or "none"
        raise LockValidationError(
            f"{name} is not installed editable from {wanted}; editable locations: {rendered}")


def _artifact_project_is_exact(
    artifact: Path,
    name: str,
    distributions: tuple[importlib.metadata.Distribution, ...],
) -> dict[str, str]:
    """Bind a non-editable project installation to one exact built artifact.

    Version equality is not enough for a fresh-install gate: another wheel with the
    same project version may contain different bytes.  PEP 610 requires installers to
    retain the local archive URL and hash in ``direct_url.json``.  Accept exactly one
    project metadata record and require both coordinates to match the artifact the
    gate just built.
    """

    artifact = artifact.resolve()
    if not artifact.is_file():
        raise LockValidationError(
            f"the selected project artifact is not a regular file: {artifact}")
    if len(distributions) != 1:
        raise LockValidationError(
            f"{name} has {len(distributions)} installed metadata copies; a fresh "
            "artifact installation requires exactly one")
    distribution = distributions[0]
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        raise LockValidationError(
            f"{name} has no direct_url.json binding it to {artifact}")
    try:
        direct = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LockValidationError(f"{name} has malformed direct_url.json") from exc
    if direct.get("dir_info", {}).get("editable") is True:
        raise LockValidationError(
            f"{name} is editable; the fresh-install lane requires its built artifact")
    parsed = urlparse(direct.get("url", ""))
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise LockValidationError(
            f"{name} was not installed from a local release artifact")
    installed_from = Path(unquote(parsed.path)).resolve()
    if installed_from != artifact:
        raise LockValidationError(
            f"{name} was installed from {installed_from}, not {artifact}")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    _installed_payload_is_wheel(distribution, artifact, name)
    return {
        "mode": "artifact",
        "path": str(artifact),
        "sha256": digest,
    }


def _installed_payload_is_wheel(
    distribution: importlib.metadata.Distribution,
    artifact: Path,
    name: str,
) -> None:
    """Prove that every installed project payload byte came from ``artifact``.

    PEP 610 binds a local archive installation to its source path, but its
    ``archive_info`` hash is optional and uv intentionally omits it for a local
    wheel.  Compare the wheel payload itself instead.  ``RECORD`` is the one
    wheel member installers rewrite when they add their own metadata, so its
    payload is excluded while every package and immutable metadata file is
    compared byte for byte.
    """

    try:
        with zipfile.ZipFile(artifact) as wheel:
            members: dict[str, bytes] = {}
            for info in wheel.infolist():
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise LockValidationError(
                        f"{name} wheel contains an unsafe member {info.filename!r}")
                if info.is_dir() or info.filename.endswith(".dist-info/RECORD"):
                    continue
                members[info.filename] = wheel.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise LockValidationError(f"cannot inspect project wheel {artifact}: {exc}") from exc
    if not members:
        raise LockValidationError(f"{name} wheel has no auditable payload")

    for relative, wanted in members.items():
        installed = Path(distribution.locate_file(relative)).resolve()
        try:
            actual = installed.read_bytes()
        except OSError as exc:
            raise LockValidationError(
                f"{name} installed payload is missing {relative!r}: {exc}") from exc
        if actual != wanted:
            raise LockValidationError(
                f"{name} installed payload differs from the wheel at {relative!r}")

    recorded = distribution.files
    if recorded is None:
        raise LockValidationError(f"{name} has no installed RECORD file list")
    project_prefix = name.replace("-", "_") + "/"
    unexpected = sorted(
        str(path) for path in recorded
        if str(path).startswith(project_prefix) and str(path) not in members
    )
    if unexpected:
        raise LockValidationError(
            f"{name} installed unexpected project payload: {', '.join(unexpected[:8])}")


def validate(lock: Path, project: Path, extras: tuple[str, ...], *,
             artifact: Path | None = None) -> dict[str, object]:
    lock = lock.resolve()
    project = project.resolve()
    project_name, project_version = _locked_project(lock)
    uv = shutil.which("uv")
    if uv is None:
        raise LockValidationError("uv is not available on PATH")
    command = [
        uv, "export", "--locked", "--offline",
        "--format", "requirements-txt", "--no-hashes", "--no-header", "--no-annotate",
    ]
    for extra in extras:
        command.extend(("--extra", extra))
    # The enclosing gate owns the command deadline and process group. Do not
    # create a shorter nested budget or import the unverified project for policy.
    completed = subprocess.run(
        command, cwd=project, text=True, capture_output=True, check=False)
    if completed.returncode:
        detail = (completed.stdout + completed.stderr).strip()
        raise LockValidationError(f"uv export failed: {detail}")
    expected = expected_versions(completed.stdout, (project_name, project_version))
    installed, distributions = installed_versions()
    compare_versions(expected, installed)
    if artifact is None:
        _editable_project_is_this_checkout(
            project, project_name, distributions[project_name])
        project_install = {"mode": "editable", "path": str(project)}
    else:
        project_install = _artifact_project_is_exact(
            artifact, project_name, distributions[project_name])
    return {
        "schema": SCHEMA,
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "extras": list(extras),
        "project_install": project_install,
        "packages": dict(sorted(installed.items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument(
        "--artifact", type=Path,
        help="require the project to come from this exact built wheel instead of "
             "an editable checkout")
    parser.add_argument("--extra", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = validate(
            args.lock, args.project, tuple(args.extra), artifact=args.artifact)
    except LockValidationError as exc:
        print(f"locked environment refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
