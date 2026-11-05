#!/bin/bash
# Build the wheel from a checkout, install it into a fresh environment outside the
# checkout. Prepare the example task, validate an Eval plan and run Sim.
#   bash scripts/check_install.sh <checkout> [output directory]
set -euo pipefail
CK=$(cd "$1" && pwd)
OUT=${2:-$(mktemp -d /tmp/behave-check-install.XXXXXX)}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cd "$CK"
uv build --wheel --out-dir "$OUT/dist" >"$OUT/build.log" 2>&1
shopt -s nullglob
WHEELS=("$OUT"/dist/behave-*.whl)
if ((${#WHEELS[@]} != 1)); then
  echo "expected exactly one wheel in $OUT/dist" >&2
  exit 1
fi
uv venv --python 3.11.15 "$OUT/venv" >"$OUT/venv.log" 2>&1
uv pip install --python "$OUT/venv/bin/python" "${WHEELS[0]}[eda]" >"$OUT/install.log" 2>&1
BIN=$OUT/venv/bin
# An installed package has no checkout to default to (docs/install.md).
export BEHAVE_WORK_DIR=$OUT/work BEHAVE_EDA_ROOT=${BEHAVE_EDA_ROOT:-$CK/external/oss-cad-suite}
unset PYTHONPATH BEHAVE_REPO_ROOT BEHAVE_DATA_DIR
cd "$OUT"
for c in behave-infra behave-eval behave-train behave-trainer behave-data behave-inspect behave-release; do
  "$BIN/$c" --help >"$OUT/help.$c.txt"
done
"$BIN/python" "$CK/examples/prepare_task.py" "$OUT/releases/local-inputs" >"$OUT/freeze.json"
cp "$CK/configs/eval/local.example.json" "$OUT/my-eval.json"
"$BIN/behave-eval" prepare --configuration "$OUT/my-eval.json" --task-objects "$OUT/releases/local-inputs/objects" \
  --release "$OUT/releases/local-inputs/release.json" --task passthrough --output "$OUT/prepared-eval" >"$OUT/prepare.json"
"$BIN/behave-eval" --plan "$OUT/prepared-eval/plan.json" --task-objects "$OUT/prepared-eval/inputs" --dry-run >"$OUT/dryrun.json"
"$BIN/python" "$CK/examples/public_sim_consumer.py" "$OUT/sim-example" >"$OUT/sim-example.json"
echo "install check passed: $OUT"
