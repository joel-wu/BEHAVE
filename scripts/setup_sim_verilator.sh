#!/usr/bin/env bash
# Explicit, versioned compiler installation. No candidate run builds tools.
# Existing executables and failed build material are retained, never removed.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
prefix="${BEHAVE_EDA_ROOT:-${BEHAVE_EDA_EXTERNAL_DIR:-${repo}/external}/oss-cad-suite}"
revision='9cddf46932de95cd520877d334a78f242750c6a8'
executable='behave-verilator-schedule-refs-1'
marker='(behave-schedule-refs-1)'

check_version() {
    local actual
    actual="$("$1" --version)"
    [[ "$actual" == "Verilator 5.051 "*"$marker"* ]] || {
        echo "Sim Verilator revision differs: $actual" >&2
        return 1
    }
}

case "${1-}" in
    --check) check_version "$prefix/libexec/$executable"; exit ;;
    "") ;;
    *) echo "usage: bash $0 [--check]" >&2; exit 2 ;;
esac

if [[ -e "$prefix/libexec/$executable" ]]; then
    check_version "$prefix/libexec/$executable"
    echo "Sim Verilator already installed: $prefix/libexec/$executable"
    exit
fi

build="${BEHAVE_SIM_VERILATOR_BUILD_DIR:-}"
if [[ -z "$build" ]]; then
    build="$(mktemp -d "${BEHAVE_SCRATCH_DIR:-/tmp}/behave-sim-verilator.XXXXXX")"
fi
source="$build/source"
if [[ ! -d "$source" ]]; then
    mkdir -p "$source"
    curl -fL -o "$build/source.tar.gz" \
        "https://codeload.github.com/verilator/verilator/tar.gz/$revision"
    tar -xzf "$build/source.tar.gz" --strip-components=1 -C "$source"
    patch -d "$source" -p1 < "$repo/native/verilator/condition-effects.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/control-resolver.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/case-effects.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/short-circuit-effects.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/dfg-selection.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/graph-path.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/loop-report.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/call-effects.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/helper-nba.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/ref-alias.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/assert-actions.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/case-failures.patch"
    patch -d "$source" -p1 < "$repo/native/verilator/schedule-refs.patch"
fi

echo "Building Sim Verilator in $source"
(
    cd "$source"
    if [[ ! -f configure ]]; then autoconf; fi
    ./configure --enable-ccwarn --prefix="$prefix"
    make -C src --jobs="${CMAKE_BUILD_PARALLEL_LEVEL:-1}" opt
)
check_version "$source/bin/verilator_bin"

# A new pathname leaves both old executable bytes and their users intact.
# Publishing via a hard link cannot overwrite another installer's result.
mkdir -p "$prefix/libexec/.compiler-staging"
publication="$(mktemp "$prefix/libexec/.compiler-staging/$executable.XXXXXX")"
install -m 755 "$source/bin/verilator_bin" "$publication"
ln "$publication" "$prefix/libexec/$executable"
check_version "$prefix/libexec/$executable"
echo "Installed $prefix/libexec/$executable (old compiler retained)"
