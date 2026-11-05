#!/usr/bin/env bash
# Install the EDA toolchain required by the verifier: verilator, yosys and
# iverilog. These are not available from PyPI.
#
# A single OSS CAD Suite release supplies all three at mutually consistent
# versions without elevated privileges. The source-event exporter is built
# separately against its pinned Slang source by setup_rtl_frontend.sh. Sim's
# qualified Verilator executable is installed alongside the stock compiler.
# The resolved
# release is recorded in external/eda.lock so that subsequent installations on
# other hosts reproduce the same tool versions.
#
# Usage:
#   scripts/setup_eda.sh                 # install the release named in external/eda.lock,
#                                        # or resolve the latest release and write the lock
#   EDA_RELEASE=2026-07-01 scripts/setup_eda.sh   # install a specific release
#   scripts/setup_eda.sh --check         # validate the existing installation only
#
# After installation, add the toolchain to the current shell with:
#   source external/oss-cad-suite/environment

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The override is intentionally limited to the installation root. It lets the
# fail-closed validator be tested without touching a real managed toolchain.
EXTERNAL_DIR="${BEHAVE_EDA_EXTERNAL_DIR:-${REPO_ROOT}/external}"
LOCK_FILE="${EXTERNAL_DIR}/eda.lock"
INSTALL_DIR="${EXTERNAL_DIR}/oss-cad-suite"
RELEASES_API="https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest"
CHECK_ONLY=false

case "${1-}" in
    "") ;;
    --check) CHECK_ONLY=true ;;
    *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

case "$(uname -m)" in
    x86_64)          ARCH="linux-x64" ;;
    aarch64 | arm64) ARCH="linux-arm64" ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

lock_value() {
    local key="$1"
    sed -n "s/^${key}[[:space:]]*=[[:space:]]*\"\([^\"]*\)\"[[:space:]]*$/\1/p" "${LOCK_FILE}"
}

LOCK_RELEASE=""
LOCK_ARCH=""
if [[ -f "${LOCK_FILE}" ]]; then
    LOCK_RELEASE="$(lock_value release)"
    LOCK_ARCH="$(lock_value arch)"
    [[ -n "${LOCK_RELEASE}" && -n "${LOCK_ARCH}" ]] || {
        echo "${LOCK_FILE} must contain one quoted release and arch" >&2
        exit 1
    }
fi

# Release selection order: explicit override, then the lock file, then the
# newest upstream release.
RELEASE="${EDA_RELEASE:-}"
if [[ -z "${RELEASE}" && -n "${LOCK_RELEASE}" ]]; then
    RELEASE="${LOCK_RELEASE}"
    if [[ "${LOCK_ARCH}" != "${ARCH}" ]]; then
        echo "locked EDA architecture ${LOCK_ARCH} does not match this host (${ARCH})" >&2
        exit 1
    fi
fi
if [[ -z "${RELEASE}" ]]; then
    if ${CHECK_ONLY}; then
        echo "cannot check an EDA installation without ${LOCK_FILE}" >&2
        exit 1
    fi
    echo "resolving the latest OSS CAD Suite release"
    RELEASE="$(curl -fsSL "${RELEASES_API}" | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    [[ -n "${RELEASE}" ]] || { echo "failed to resolve a release tag" >&2; exit 1; }
fi
[[ "${RELEASE}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || {
    echo "invalid OSS CAD Suite release: ${RELEASE}" >&2
    exit 1
}

# Release tags are dates (YYYY-MM-DD); the asset name uses the compact form.
COMPACT="${RELEASE//-/}"
TARBALL="oss-cad-suite-${ARCH}-${COMPACT}.tgz"
URL="https://github.com/YosysHQ/oss-cad-suite-build/releases/download/${RELEASE}/${TARBALL}"

validate_install() {
    local directory="$1"
    local installed_release description tool
    [[ -f "${directory}/VERSION" ]] || {
        echo "EDA installation has no VERSION file: ${directory}" >&2
        return 1
    }
    installed_release="$(tr -d '[:space:]' < "${directory}/VERSION")"
    [[ "${installed_release}" == "${COMPACT}" ]] || {
        echo "EDA installation release ${installed_release} does not match ${COMPACT}" >&2
        return 1
    }
    for tool in verilator yosys iverilog; do
        [[ -x "${directory}/bin/${tool}" ]] || {
            echo "EDA installation is missing executable ${tool}" >&2
            return 1
        }
    done
    [[ -f "${directory}/libexec/verilator_bin" ]] || {
        echo "EDA installation is missing the Verilator native executable" >&2
        return 1
    }
    description="$(LC_ALL=C readelf -h "${directory}/libexec/verilator_bin" 2>/dev/null)" || {
        echo "EDA installation architecture does not match ${ARCH}: not an ELF executable" >&2
        return 1
    }
    description="${description,,}"
    case "${ARCH}" in
        linux-x64) [[ "${description}" == *"x86-64"* ]] ;;
        linux-arm64) [[ "${description}" == *"aarch64"* || "${description}" == *"ARM64"* ]] ;;
    esac || {
        echo "EDA installation architecture does not match ${ARCH}: ${description}" >&2
        return 1
    }
    "${directory}/bin/verilator" --version >/dev/null
    "${directory}/bin/yosys" --version >/dev/null
    "${directory}/bin/iverilog" -V >/dev/null 2>&1
}

if [[ -e "${INSTALL_DIR}" ]]; then
    validate_install "${INSTALL_DIR}" || {
        echo "refusing to reuse an unverified EDA installation; move it aside and rerun" >&2
        exit 1
    }
    echo "validated ${RELEASE} ${ARCH} toolchain at ${INSTALL_DIR}"
elif ${CHECK_ONLY}; then
    echo "EDA toolchain is not installed at ${INSTALL_DIR}" >&2
    exit 1
else
    mkdir -p "${EXTERNAL_DIR}"
    STAGING="$(mktemp -d "${EXTERNAL_DIR}/.oss-cad-suite-install.XXXXXX")"
    ARCHIVE="${STAGING}/${TARBALL}"
    echo "downloading ${TARBALL}"
    curl -fL --progress-bar -o "${ARCHIVE}" "${URL}"
    echo "extracting and validating ${RELEASE} ${ARCH}"
    tar -xzf "${ARCHIVE}" -C "${STAGING}"
    validate_install "${STAGING}/oss-cad-suite"
    mv "${STAGING}/oss-cad-suite" "${INSTALL_DIR}"
    echo "installation archive retained in ${STAGING}"
fi

if ! ${CHECK_ONLY}; then
    LOCK_TEMP="$(mktemp "${EXTERNAL_DIR}/.eda.lock.XXXXXX")"
    cat > "${LOCK_TEMP}" <<EOF
# Resolved by scripts/setup_eda.sh. Commit this file so that other hosts install
# the same toolchain versions.
release = "${RELEASE}"
arch = "${ARCH}"
EOF
    mv "${LOCK_TEMP}" "${LOCK_FILE}"
fi

echo
echo "installed:"
for tool in verilator yosys iverilog; do
    case "${tool}" in
        iverilog) ver="$("${INSTALL_DIR}/bin/iverilog" -V 2>&1)" ;;
        *) ver="$("${INSTALL_DIR}/bin/${tool}" --version 2>&1)" ;;
    esac
    printf "  %-10s %s\n" "${tool}" "${ver%%$'\n'*}"
done
echo
echo "add to the current shell with: source ${INSTALL_DIR}/environment"

# Native observation needs Slang's analyzed process reads, which its stock CLI
# does not export. Install our small compiler client once, never during a run.
if ${CHECK_ONLY}; then
    BEHAVE_EDA_ROOT="${INSTALL_DIR}" bash "${REPO_ROOT}/scripts/setup_sim_verilator.sh" --check
    BEHAVE_EDA_ROOT="${INSTALL_DIR}" bash "${REPO_ROOT}/scripts/setup_rtl_frontend.sh" --check
else
    BEHAVE_EDA_ROOT="${INSTALL_DIR}" bash "${REPO_ROOT}/scripts/setup_sim_verilator.sh"
    BEHAVE_EDA_ROOT="${INSTALL_DIR}" bash "${REPO_ROOT}/scripts/setup_rtl_frontend.sh"
fi
