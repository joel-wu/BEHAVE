"""Detached infra fixtures against a byte-frozen source snapshot.

This is a development runner, not a release builder. The selected interpreter
and external EDA installation are reused explicitly, not copied or certified.
Operator Python sources are included for test collection, not their measurement
archives. No task bodies, credentials, live models or production training jobs
enter this snapshot. Held-out ID lists are included for training admission. Tiny native
training fixtures require explicit CPU/GPU lane selection.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parents[1]
if __name__ == "__main__" and sys.argv[1:2] != ["_installed_pytest"]:
    # Source validation boots without an editable install; the installed lane
    # must instead keep its wheel imports and never prepend checkout/src.
    sys.path[:0] = [str(REPO), str(REPO / "src"), str(REPO / "tools")]

from behave.runtime import snapshots
from behave.runtime.snapshots import install_runtime

TREES = ("src/behave", "tests/infrastructure")
SHARED_TESTS = ("tests/sim/test_public_import_boundary.py", "tests/sim/test_rtl_tool_isolation.py",
                "tests/sim/test_rtl_execution_policy.py", "tests/sim/test_rtl_native_preparation.py",
                "tests/sim/test_rtl_observation_binding.py", "tests/sim/test_rtl_replay_failure.py",
                "tests/sim/test_analysis_report_handoff.py", "tests/sim/test_command_handler.py")
TEST_SUPPORT = ("tests/__init__.py", "tests/sim/__init__.py", "tests/support/__init__.py",
                "tests/sim/test_rtl_source_plan.py",
                "tests/sim/test_api_path.py", "tests/support/device_boundary.py", "tests/support/reference.py",
                "tests/sim/test_protocol_adapters.py", "tests/sim/test_task_interface.py",
                "tests/sim/test_apb_protocol.py", "tests/sim/test_axi_lite_protocol.py",
                "tests/sim/test_interface_composition.py")
FILES = (*snapshots.FILES, *SHARED_TESTS, *TEST_SUPPORT)


def _validation_files(repo: Path) -> tuple[str, ...]:
    # The suite tests operator compatibility as well as installed package code.
    # Keep its Python closure current without copying tools' recorded outputs.
    operators = repo / "tools"
    if operators.is_symlink():
        raise ValueError("validation tools directory must not be a symlink")
    sources = (path.relative_to(repo).as_posix() for path in operators.rglob("*.py")
               if "__pycache__" not in path.parts)
    return tuple(sorted(set(FILES).union(sources)))


def _contents(repo: Path, extra_files: tuple[str, ...] = (), *, files=None) -> dict[str, bytes]:
    return snapshots._contents(repo, extra_files, files=_validation_files(repo) if files is None else files, trees=TREES)


def freeze(repo: Path, target: Path, *, extra_files: tuple[str, ...] = (), overrides=None) -> dict:
    """Validation recipe over the shared snapshot implementation."""
    return snapshots.freeze(repo, target, extra_files=extra_files, overrides=overrides,
                            files=_validation_files(repo), trees=TREES)


_verified_snapshot = snapshots.read_snapshot
verify = snapshots.verify
stage = snapshots.stage


def submit(job_id: str, selection: str = "", markers: str = "", native_python: str = "", *, wall_seconds: int | None = None,
           installed: bool = False, gpus: int = 0, cpus: int = 8, ppa_root: str = "") -> dict:
    from behave.runtime.jobs import submit as detached_submit

    if native_python and (not Path(native_python).is_absolute() or not Path(native_python).is_file()):
        raise ValueError("native training lane requires an explicit existing absolute Python executable")
    if type(gpus) is not int or gpus < 0 or (gpus and not native_python):
        raise ValueError("GPU validation requires a native interpreter and an explicit GPU count")
    if ppa_root:
        root = Path(ppa_root)
        if not root.is_absolute() or not all((root / name).is_file()
                for name in ("sky130.lib", "opensta/usr/bin/sta")):
            raise ValueError("PPA validation requires an absolute installation with Liberty and OpenSTA")
    output = REPO / "work" / ("infra_worker_" + uuid.uuid4().hex[:12])
    snapshot = output / "snapshot"
    record = freeze(REPO, snapshot)
    run = output / "validation"
    from behave.agent.jobs import JobControl, JobRequest
    from behave.core import runtime_defaults as defaults

    wall_seconds = defaults.VALIDATION_WALL_SECONDS if wall_seconds is None else wall_seconds
    request = JobRequest(validation_command(snapshot, run, selection, markers, native_python,
                         installed=installed, gpus=gpus, cpus=cpus, wall_seconds=wall_seconds, ppa_root=ppa_root),
                         str(REPO), wall_seconds=wall_seconds, cpus=cpus, allocation=int(job_id), network="host", gpus=gpus,
                         cleanup_seconds=defaults.JOB_CLEANUP_SECONDS)
    job_directory = output / "job"
    key = JobControl(job_directory).create(request)
    argv = [*request.slurm_prefix(overlap=True), sys.executable, "-I", "-B",
            str(snapshot / "tools/infra_job_worker.py"), "run", str(job_directory), key]
    return detached_submit(run, argv, cwd=REPO,
                           wall_seconds=wall_seconds + request.cleanup_seconds + defaults.JOB_SLURM_EXIT_SECONDS,
                           metadata={"purpose": "frozen eval/train infra fixtures", "allocation": int(job_id),
                                     "cpus": cpus, "snapshot": record["sha256"], "selection": selection, "markers": markers,
                                     "job_directory": str(job_directory), "job_key": key,
                                     "native_python": native_python or None, "wall_seconds": wall_seconds,
                                     "installed": installed, "gpus": gpus, "ppa_root": ppa_root or None})


def validation_command(snapshot: Path, run: Path, selection: str, markers: str, native_python: str = "", *,
                       installed: bool = False, gpus: int = 0, cpus: int = 8,
                       wall_seconds: int | None = None, ppa_root: str = "") -> tuple[str, ...]:
    """Explicit toolchain/cache configuration, not an inherited login environment."""
    from behave.core import runtime_defaults as defaults

    wall_seconds = defaults.VALIDATION_WALL_SECONDS if wall_seconds is None else wall_seconds
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("the infrastructure validation lane requires uv")
    def runtime_path(kind: str) -> str:
        value = subprocess.run((uv, kind, "dir"), check=True, capture_output=True, text=True,
                               timeout=defaults.TOOL_INSPECTION_SECONDS).stdout.strip()
        if not Path(value).is_absolute():
            raise RuntimeError(f"uv did not identify an absolute {kind} directory")
        return value

    cache, interpreters = runtime_path("cache"), runtime_path("python")
    paths = tuple(dict.fromkeys((str(Path(sys.executable).parent), str(REPO / "external/oss-cad-suite/bin"),
                                str(Path(uv).parent), "/usr/bin", "/bin")))
    return ("/usr/bin/env", "PATH=" + ":".join(paths), "UV_CACHE_DIR=" + cache,
            "UV_PYTHON_INSTALL_DIR=" + interpreters, "UV_PYTHON=" + sys.executable,
            "BEHAVE_EDA_ROOT=" + str(REPO / "external/oss-cad-suite"),
            "BEHAVE_TEST_TRAINING_GPUS=" + str(gpus),
            "BEHAVE_TEST_TRAINING_CPUS=" + str(cpus),
            "BEHAVE_TEST_SUITE_SECONDS=" + str(wall_seconds),
            *(("BEHAVE_PPA_ROOT=" + ppa_root,) if ppa_root else ()),
            sys.executable, "-I", "-B", str(snapshot / "tools/infra_validation_job.py"),
            "_run", str(snapshot), str(run / "junit.xml"), selection, markers,
            *((native_python,) if native_python or installed else ()), *(("--installed",) if installed else ()))


def _run(snapshot: Path, report: str, selection: str, markers: str = "", native_python: str = "", *,
         installed: bool = False) -> int:
    # The durable snapshot is evidence, not an import filesystem. Small random
    # Python reads over NFS can otherwise dominate a bounded worker's lifetime.
    # Stage and verify exactly the frozen files, retaining the original receipt.
    with TemporaryDirectory(prefix="behave-infra-tests-", dir="/tmp") as scratch:
        local = Path(scratch) / "checkout"
        stage(snapshot, local)
        if installed:
            return _installed_run(local, Path(report), selection, markers, native_python)
        command = snapshots.source_command(local,
            "from tools.infra_validation_job import _pytest; "
            "raise SystemExit(_pytest(source,*sys.argv[1:]))", report, selection, markers, native_python)
        env = {**os.environ, "PYTHONPATH": str(local / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(command, cwd=scratch, env=env, check=False).returncode


def _pytest(local: Path, report: str, selection: str, markers: str, native_python: str) -> int:
    # Operator tools and shared test fixtures are not wheel packages. Make that
    # closed snapshot importable without adding its src/ to the installed lane.
    sys.path.insert(0, str(local))
    if native_python:
        os.environ["BEHAVE_NATIVE_TRAINER_PYTHON"] = native_python
    else:
        os.environ.pop("BEHAVE_NATIVE_TRAINER_PYTHON", None)
    lane = "native_training" if native_python else "not native_training"
    gpu_lane = int(os.environ.get("BEHAVE_TEST_TRAINING_GPUS", "0")) > 0
    cluster_lane = bool(os.environ.get("BEHAVE_TEST_TRAINING_CLUSTER"))
    lane += " and cluster_training" if cluster_lane else " and not cluster_training"
    lane += " and gpu_training" if gpu_lane else " and not gpu_training"
    markers = f"({markers}) and ({lane})" if markers else lane
    import pytest

    class Failures:
        def pytest_runtest_logreport(self, report):
            if report.failed:
                print(f"\nFAILED {report.nodeid}\n{report.longrepr}", flush=True)

    return pytest.main([
        "--rootdir=" + str(local), "--confcutdir=" + str(local / "tests/infrastructure"),
        "--basetemp=" + str(local.parent / "pytest-temp"),
        str(local / "tests/infrastructure"), *(str(local / name) for name in SHARED_TESTS),
        "-n", "0" if gpu_lane or cluster_lane else "4", "-q", "-o", "cache_dir=" + str(local.parent / "pytest-cache"), "--junitxml=" + report,
        *(["-k", selection] if selection else []), "-m", markers,
    ], plugins=[Failures()])


def _installed_run(local: Path, report: Path, selection: str, markers: str, native_python: str, *,
                   native_directory: Path | None = None) -> int:
    """Run fixtures against the same installed artifact used by real training."""
    installed = install_runtime(local, report.parent, native_python, native_directory=native_directory)
    evidence, env = installed["evidence"], installed["environment"]
    evidence.update(selection=selection, markers=markers,
                    gpus=int(os.environ.get("BEHAVE_TEST_TRAINING_GPUS", "0")))
    (report.parent / "installed.json").write_text(json.dumps(evidence, indent=2) + "\n")
    python = Path(evidence["control_python"])
    native_python = evidence["native_python"] or ""
    env["BEHAVE_TEST_INSTALLED_PREFIX"] = str(python.parent.parent)
    completed = subprocess.run((str(python), "-I", "-B", str(local / "tools/infra_validation_job.py"),
        "_installed_pytest", str(local), str(report), selection, markers, native_python),
        cwd=local.parent, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    if len(sys.argv) == 7 and sys.argv[1] == "_installed_pytest":
        import behave

        if not Path(behave.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise RuntimeError("installed validation imported a checkout instead of its wheel")
        raise SystemExit(_pytest(Path(sys.argv[2]), *sys.argv[3:]))
    installed = sys.argv[-1:] == ["--installed"] and sys.argv[1:2] == ["_run"]
    if installed:
        sys.argv.pop()
    if len(sys.argv) in {5, 6, 7} and sys.argv[1] == "_run":
        raise SystemExit(_run(Path(sys.argv[2]), sys.argv[3], sys.argv[4],
                             sys.argv[5] if len(sys.argv) >= 6 else "",
                             sys.argv[6] if len(sys.argv) == 7 else "", installed=installed))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", type=int)
    parser.add_argument("--select", default="", help="pytest -k expression; omitted means the complete fixture lane")
    parser.add_argument("--markers", default="", help="pytest -m expression; excluded dependency lanes are not passes")
    parser.add_argument("--native-python", default="", help="opt-in native training lane with this pinned interpreter; CPU unless --gpus")
    parser.add_argument("--gpus", type=int, default=0, help="opt-in allocated GPU training lane; serial fixtures, no CPU fallback")
    parser.add_argument("--cpus", type=int, default=8, help="allocated CPUs, including native orchestration headroom")
    parser.add_argument("--installed", action="store_true", help="build/install a wheel offline, then run fixtures against it")
    parser.add_argument("--ppa-root", default="", help="explicit PPA installation containing sky130.lib and opensta/usr/bin/sta")
    parser.add_argument("--wall-seconds", type=int, default=None,
                        help="whole-job budget; default: runtime VALIDATION_WALL_SECONDS; does not change individual tests")
    arguments = parser.parse_args()
    print(json.dumps(submit(str(arguments.job_id), arguments.select, arguments.markers, arguments.native_python,
                            wall_seconds=arguments.wall_seconds, installed=arguments.installed,
                            gpus=arguments.gpus, cpus=arguments.cpus, ppa_root=arguments.ppa_root), indent=2))
