#!/usr/bin/env bash
# Explicit installation only. Candidate preparation never downloads or builds tools.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
prefix="${BEHAVE_EDA_ROOT:-${BEHAVE_EDA_EXTERNAL_DIR:-${repo}/external}/oss-cad-suite}"
expected='behave-rtl-frontend/23 slang-source=8acc660a2'
executable='behave-rtl-frontend-23'
case "${1-}" in
    --check)
        actual="$("${prefix}/libexec/${executable}" --version)"
        [[ "${actual}" == "${expected}" ]] || {
            echo "native RTL frontend revision differs: ${actual}" >&2
            exit 1
        }
        exit 0 ;;
    "") ;;
    *) echo "usage: bash $0 [--check]" >&2; exit 2 ;;
esac

# A caller can retain an explicit build directory for subsequent rebuilds.
# Otherwise use node-local storage, never the EDA/NFS installation directory.
build="${BEHAVE_RTL_FRONTEND_BUILD_DIR:-}"
if [[ -z "${build}" ]]; then
    build="$(mktemp -d "${BEHAVE_SCRATCH_DIR:-/tmp}/behave-rtl-frontend-build.XXXXXX")"
fi
echo "Building RTL frontend in ${build}"
cmake -S "${repo}/native/rtl_frontend" -B "${build}" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="${prefix}"
cmake --build "${build}" --target behave-rtl-frontend \
    --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-1}"
cmake --install "${build}" --prefix "${build}/install"
actual="$("${build}/install/libexec/behave-rtl-frontend" --version)"
[[ "${actual}" == "${expected}" ]] || {
    echo "built RTL frontend revision differs: ${actual}" >&2
    exit 1
}
# Publish this ABI revision atomically. Frozen runs using the old unversioned
# client or another revision retain their installation as well as their inode.
mkdir -p "${prefix}/libexec/.frontend-staging"
# Failed unpublished files remain here for explicit operator review/cleanup.
publication="$(mktemp "${prefix}/libexec/.frontend-staging/${executable}.XXXXXX")"
install -m 755 "${build}/install/libexec/behave-rtl-frontend" "${publication}"
mv -fT -- "${publication}" "${prefix}/libexec/${executable}"
bash "$0" --check
