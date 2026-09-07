#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [[ -n "${HANDOFF_ROOT:-}" ]]; then
  HANDOFF_ROOT=$(CDPATH= cd -- "$HANDOFF_ROOT" && pwd)
elif [[ -d "$SCRIPT_DIR/data" && -f "$SCRIPT_DIR/SHA256SUMS" ]]; then
  HANDOFF_ROOT=$SCRIPT_DIR
else
  echo "Set HANDOFF_ROOT to the mounted reddit-zst-benchmark directory." >&2
  exit 1
fi

WORK_ROOT=${WORK_ROOT:-"$HOME/benchmark-data/reddit-zst-benchmark"}
REPO_ROOT=${REPO_ROOT:-"$HOME/src/reddit-zst-to-parquet-m5-benchmark"}
THREADS=${THREADS:-12}
MEMORY_LIMIT_GB=${MEMORY_LIMIT_GB:-25}
REPETITIONS=${REPETITIONS:-3}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}

if [[ $(uname -s) != Darwin ]]; then
  echo "This benchmark handoff is configured for macOS." >&2
  exit 1
fi

for value in "$THREADS" "$MEMORY_LIMIT_GB" "$REPETITIONS"; do
  if [[ ! $value =~ ^[1-9][0-9]*$ ]]; then
    echo "THREADS, MEMORY_LIMIT_GB, and REPETITIONS must be positive integers." >&2
    exit 1
  fi
done

cd "$HANDOFF_ROOT"
shasum -a 256 -c SHA256SUMS

mkdir -p "$WORK_ROOT/data/comments" "$WORK_ROOT/data/submissions" "$HOME/src"
rsync -ah --progress \
  "$HANDOFF_ROOT/data/comments/RC_2026-05.zst" \
  "$WORK_ROOT/data/comments/"
rsync -ah --progress \
  "$HANDOFF_ROOT/data/submissions/RS_2026-05.zst" \
  "$WORK_ROOT/data/submissions/"

verify_source() {
  local path=$1
  local bytes=$2
  local sha256=$3

  if [[ $(stat -f %z "$path") != "$bytes" ]]; then
    echo "Unexpected source size: $path" >&2
    exit 1
  fi
  printf '%s  %s\n' "$sha256" "$path" | shasum -a 256 -c -
}

verify_source \
  "$WORK_ROOT/data/comments/RC_2026-05.zst" \
  47844685635 \
  b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc
verify_source \
  "$WORK_ROOT/data/submissions/RS_2026-05.zst" \
  23823383434 \
  83f25791b1d7cf8663a5c5cea46d291924adf2e00ba02b450c564711725ffcef

EXPECTED_COMMIT=$(tr -d '[:space:]' < "$HANDOFF_ROOT/COMMIT")
if [[ ! -d "$REPO_ROOT/.git" ]]; then
  git clone "$HANDOFF_ROOT/reddit-zst-to-parquet.bundle" "$REPO_ROOT"
  git -C "$REPO_ROOT" remote set-url origin https://github.com/theontho/reddit-zst-to-parquet.git
fi
if [[ -n $(git -C "$REPO_ROOT" status --short) ]]; then
  echo "Benchmark repository has local changes: $REPO_ROOT" >&2
  exit 1
fi
if [[ $(git -C "$REPO_ROOT" rev-parse HEAD) != "$EXPECTED_COMMIT" ]]; then
  echo "Benchmark repository does not match the handoff commit." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

cd "$REPO_ROOT"
uv sync --all-groups
uv run reddit-zst-to-parquet precheck --method local --skip-connection
uv run python -m benchmarks.conversion --help >/dev/null

if [[ -z "${PROTECTION:-}" ]]; then
  if command -v mdatp >/dev/null 2>&1; then
    PROTECTION=installed
  else
    PROTECTION=none
  fi
fi
if pmset -g batt | grep -q "AC Power"; then
  POWER_SOURCE=ac
else
  POWER_SOURCE=battery
fi

OUTPUT_ROOT="$REPO_ROOT/out/benchmarks/m5-max-128gb/$RUN_ID"
mkdir -p "$OUTPUT_ROOT"
{
  date -u
  sw_vers
  system_profiler SPHardwareDataType SPNVMeDataType
  sysctl -n hw.model
  sysctl -n hw.physicalcpu
  sysctl -n hw.logicalcpu
  sysctl -n hw.memsize
  sysctl -n hw.perflevel0.physicalcpu 2>/dev/null || true
  sysctl -n hw.perflevel1.physicalcpu 2>/dev/null || true
  pmset -g batt
  pmset -g custom
  df -h "$WORK_ROOT" "$REPO_ROOT"
  uv --version
  uv run python --version
  uv run python -c 'import duckdb, zstandard; print(duckdb.__version__, zstandard.__version__)'
  git rev-parse HEAD
  git status --short
  if command -v mdatp >/dev/null 2>&1; then
    mdatp health || true
  fi
} | tee "$OUTPUT_ROOT/environment.txt"

run_case() {
  local dataset=$1
  local source=$2
  local sha256=$3
  local rows=$4

  uv run python -m benchmarks.conversion \
    --source "$source" \
    --expected-source-sha256 "$sha256" \
    --output-dir "$OUTPUT_ROOT/$dataset/staged-$rows" \
    --mode staged \
    --copy-mode block \
    --decoder python-threaded \
    --rows "$rows" \
    --repetitions "$REPETITIONS" \
    --warmups 0 \
    --threads "$THREADS" \
    --memory-limit-gb "$MEMORY_LIMIT_GB" \
    --metadata "protection=$PROTECTION" \
    --metadata "power_source=$POWER_SOURCE" \
    --metadata source_filesystem=apfs \
    --metadata source_disk=internal_nvme \
    --metadata output_disk=internal_nvme
}

run_case \
  RC_2026-05 \
  "$WORK_ROOT/data/comments/RC_2026-05.zst" \
  b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  4000000
run_case \
  RC_2026-05 \
  "$WORK_ROOT/data/comments/RC_2026-05.zst" \
  b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  6000000
run_case \
  RS_2026-05 \
  "$WORK_ROOT/data/submissions/RS_2026-05.zst" \
  83f25791b1d7cf8663a5c5cea46d291924adf2e00ba02b450c564711725ffcef \
  500000
run_case \
  RS_2026-05 \
  "$WORK_ROOT/data/submissions/RS_2026-05.zst" \
  83f25791b1d7cf8663a5c5cea46d291924adf2e00ba02b450c564711725ffcef \
  1500000

uv run python - "$OUTPUT_ROOT" "$REPETITIONS" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_repetitions = int(sys.argv[2])
results = sorted(root.glob("*/staged-*/result.json"))
if len(results) != 4:
    raise SystemExit(f"Expected four result files, found {len(results)}")

for path in results:
    result = json.loads(path.read_text(encoding="utf-8"))
    runs = result["runs"]
    validation = result["validation"]
    if len(runs) != expected_repetitions:
        raise SystemExit(f"Unexpected repetition count in {path}")
    if not validation["source_sha256_matched"] or not validation["schemas_equal"]:
        raise SystemExit(f"Validation failed in {path}")
    for run in runs:
        if run["staged"]["output"]["physical_sort_violations"] != 0:
            raise SystemExit(f"Sort validation failed in {path}")
    summary = result["summary"]["staged"]
    print(
        f"{path.parent.parent.name} {validation['rows']:,} rows: "
        f"{summary['median_total_seconds']:.2f}s, "
        f"{summary['median_rows_per_second']:,.0f} rows/s"
    )
PY

HOST_NAME=$(scutil --get LocalHostName 2>/dev/null || hostname -s)
RESULT_DEST="$HANDOFF_ROOT/results/$HOST_NAME/$RUN_ID"
mkdir -p "$RESULT_DEST"
rsync -ah "$OUTPUT_ROOT/" "$RESULT_DEST/"
sync

echo "Validated results copied to $RESULT_DEST"
