"""Build a pinned, activity-capable OpenSTA without root or system changes.

Run under the detached job supervisor on Ubuntu 24.04. Build in node-local
scratch, then publish a new installation directory atomically. Historical
installations are never replaced. This installs Liberty, not a physical PDK.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from ppa_bootstrap import ASSETS

from behave.core import runtime_defaults as defaults
from behave.core.paths import scratch_root

REVISION = "8a972c9021db6975d838285286bd38fba94dd8dd"
SOURCES = {
    "opensta.tar.gz": (f"https://codeload.github.com/parallaxsw/OpenSTA/tar.gz/{REVISION}",
        "5e399097595f87843cc6ec642b6f1dee7137775e0db2dbaad5e94ba8cd800dba"),
    "cudd.tar.gz": ("https://raw.githubusercontent.com/davidkebo/cudd/main/cudd_versions/cudd-3.0.0.tar.gz",
        "b8e966b4562c96a03e7fbea239729587d7b395d53cadcc39a7203b49cf7eeb69"),
    "sky130.lib": ASSETS["sky130.lib"],
}
# Ubuntu Noble package bytes, extracted locally; no apt installation or hooks.
PACKAGES = {
    "pool/main/m/m4/m4_1.4.19-4build1_amd64.deb":
        "30ea715845a1863abd55d11e47ab91513130bf347852d4cdeae69e9d98fa795c",
    "pool/main/b/bison/bison_3.8.2+dfsg-1build2_amd64.deb":
        "18f487c400da3e968a44ecb3783a443641bcd14e2b080ed9ed1167f4aa2c1612",
    "pool/main/f/flex/flex_2.6.4-8.2build1_amd64.deb":
        "c5604f55827b50e3f568b4731b7799f86b147454c867be02bc141d9d8ce561ff",
    "pool/main/f/flex/libfl-dev_2.6.4-8.2build1_amd64.deb":
        "5d3438b7a8bd82c8e5859dbf2fefd7523a204eff8ed5f5736fe732047e938be0",
    "pool/universe/s/swig/swig_4.2.0-2ubuntu1_amd64.deb":
        "5925dc6e25348bdb350c44a9157da350ef608666b2a21f7057222e8b6aebd61d",
    "pool/universe/e/eigen3/libeigen3-dev_3.4.0-4_all.deb":
        "5755dbf2b165d2439a32d0655cd68db885a2635ce1dfb072ef0e813ccc9b2d43",
    "pool/main/t/tcl8.6/tcl8.6-dev_8.6.14+dfsg-1build1_amd64.deb":
        "82160ed9f621f93b0c3001d86bec91844030c809812e1da73192bc0e0e269162",
    "pool/main/z/zlib/zlib1g-dev_1.3.dfsg-3.1ubuntu2_amd64.deb":
        "7360405fbf49e35dca2f7a4ebeac7d4fe5fe3b2db99adac70e11e104f53b2642",
}
COMMANDS = ("read_vcd", "read_saif", "report_activity_annotation", "report_power")


def fetch(destination: Path, url: str, digest: str) -> None:
    with urllib.request.urlopen(url, timeout=defaults.FETCH_SECONDS) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"download digest mismatch: {destination.name}")
    destination.write_bytes(data)


def check(executable: Path, directory: Path) -> dict:
    script = directory / "capabilities.tcl"
    script.write_text("\n".join(
        f'puts "BEHAVE_CAPABILITY {name} [llength [info commands {name}]]"' for name in COMMANDS
    ) + "\nexit\n")
    result = subprocess.run([str(executable), "-no_init", "-exit", str(script)],
        check=True, capture_output=True, text=True, cwd=directory)
    if any(f"BEHAVE_CAPABILITY {name} 1" not in result.stdout for name in COMMANDS):
        raise RuntimeError("built OpenSTA lacks required activity commands: " + result.stdout)
    return {"commands": list(COMMANDS), "stdout": result.stdout,
            "binary_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}


def install(destination: Path, jobs: int) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("use a fresh installation directory; old tools are immutable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="behave-opensta-", dir=scratch_root()) as scratch:
        work = Path(scratch)
        downloads, deps = work / "downloads", work / "deps"
        downloads.mkdir()
        for name, (url, digest) in SOURCES.items():
            print("fetch", name, flush=True)
            fetch(downloads / name, url, digest)
        for path, digest in PACKAGES.items():
            package = downloads / Path(path).name
            fetch(package, "https://archive.ubuntu.com/ubuntu/" + path, digest)
            subprocess.run(["dpkg-deb", "-x", str(package), str(deps)], check=True)
        for name in ("opensta.tar.gz", "cudd.tar.gz"):
            with tarfile.open(downloads / name) as archive:
                archive.extractall(work, filter="data")
        source, cudd = work / f"OpenSTA-{REVISION}", work / "cudd-3.0.0"
        env = {**os.environ, "PATH": str(deps / "usr/bin") + ":/usr/bin:/bin",
               "M4": str(deps / "usr/bin/m4"),
               "BISON_PKGDATADIR": str(deps / "usr/share/bison"),
               "SWIG_LIB": str(deps / "usr/share/swig4.0")}
        subprocess.run(["./configure", "--disable-shared", "--enable-static"],
            cwd=cudd, env=env, check=True)
        subprocess.run(["make", f"-j{jobs}"], cwd=cudd, env=env, check=True)
        build = work / "build"
        subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release", f"-DCUDD_DIR={cudd}",
            f"-DCMAKE_PREFIX_PATH={deps / 'usr'}", "-DUSE_TCL_READLINE=OFF",
            f"-DTCL_HEADER={deps / 'usr/include/tcl8.6/tcl.h'}",
            "-DTCL_LIBRARY=/usr/lib/x86_64-linux-gnu/libtcl8.6.so.0",
            f"-DZLIB_INCLUDE_DIR={deps / 'usr/include'}",
            "-DZLIB_LIBRARY=/usr/lib/x86_64-linux-gnu/libz.so.1",
            f"-DFLEX_INCLUDE_DIR={deps / 'usr/include'}"],
            env=env, check=True)
        subprocess.run(["cmake", "--build", str(build), "--parallel", str(jobs)],
            env=env, check=True)
        receipt = {"schema": "behave.ppa-opensta-install/1", "revision": REVISION,
                   "sources": SOURCES, "packages": PACKAGES,
                   "compiler": subprocess.check_output(["g++", "--version"], text=True),
                   **check(build / "sta", work)}
        # Publish only complete bytes; retain sources and upstream license notices.
        with tempfile.TemporaryDirectory(prefix=".ppa-install-", dir=destination.parent) as staging:
            image = Path(staging) / "image"
            target = image / "opensta/usr/bin/sta"
            target.parent.mkdir(parents=True)
            shutil.copy2(build / "sta", target)
            shutil.copy2(downloads / "sky130.lib", image / "sky130.lib")
            shutil.copytree(downloads, image / "sources")
            shutil.copy2(source / "LICENSE", image / "OPENSTA_LICENSE")
            (image / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            image.rename(destination)
    print(json.dumps({"installed": str(destination), "revision": REVISION,
                      "binary_sha256": receipt["binary_sha256"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--jobs", type=int, default=defaults.EDA_BUILD_JOBS)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error("jobs must be between 1 and 32")
    install(args.directory, args.jobs)
