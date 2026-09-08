#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [[ -n "${HANDOFF_ROOT:-}" ]]; then
  HANDOFF_ROOT=$(CDPATH= cd -- "$HANDOFF_ROOT" && pwd)
elif [[ -d "$SCRIPT_DIR/data" && -f "$SCRIPT_DIR/SHA256SUMS" ]]; then
  HANDOFF_ROOT=$SCRIPT_DIR
else
  echo "Set HANDOFF_ROOT to an internal-NVMe copy of the benchmark handoff." >&2
  exit 1
fi

REPO_ROOT=${REPO_ROOT:-"$HANDOFF_ROOT/reddit-zst-to-parquet-linux-benchmark"}
THREADS=${THREADS:-4}
MEMORY_LIMIT_GB=${MEMORY_LIMIT_GB:-25}
REPETITIONS=${REPETITIONS:-3}
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}

if [[ $(uname -s) != Linux ]]; then
  echo "This benchmark runner is configured for Linux." >&2
  exit 1
fi

for value in "$THREADS" "$MEMORY_LIMIT_GB" "$REPETITIONS"; do
  if [[ ! $value =~ ^[1-9][0-9]*$ ]]; then
    echo "THREADS, MEMORY_LIMIT_GB, and REPETITIONS must be positive integers." >&2
    exit 1
  fi
done

cd "$HANDOFF_ROOT"
sha256sum -c SHA256SUMS

verify_source() {
  local path=$1
  local bytes=$2
  local sha256=$3

  if [[ $(stat -c %s "$path") != "$bytes" ]]; then
    echo "Unexpected source size: $path" >&2
    exit 1
  fi
  printf '%s  %s\n' "$sha256" "$path" | sha256sum -c -
}

verify_source \
  "$HANDOFF_ROOT/data/comments/RC_2026-05.zst" \
  47844685635 \
  b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc
verify_source \
  "$HANDOFF_ROOT/data/submissions/RS_2026-05.zst" \
  23823383434 \
  83f25791b1d7cf8663a5c5cea46d291924adf2e00ba02b450c564711725ffcef

EXPECTED_COMMIT=$(tr -d '[:space:]' < "$HANDOFF_ROOT/COMMIT")
if [[ ! -d "$REPO_ROOT/.git" ]]; then
  git clone "$HANDOFF_ROOT/reddit-zst-to-parquet.bundle" "$REPO_ROOT"
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
  echo "uv is required but was not found on PATH." >&2
  exit 1
fi

cd "$REPO_ROOT"
uv sync --all-groups
uv run reddit-zst-to-parquet precheck --method local --skip-connection
uv run python -m benchmarks.conversion --help >/dev/null

CPU_GOVERNOR=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo unknown)
ENERGY_PREFERENCE=$(
  cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null || echo unknown
)
SOURCE_FILESYSTEM=$(findmnt -no FSTYPE -T "$HANDOFF_ROOT")
SOURCE_DEVICE=$(findmnt -no SOURCE -T "$HANDOFF_ROOT" | sed 's/\[.*//')
SOURCE_DISK=$(lsblk -ndo MODEL "$SOURCE_DEVICE" 2>/dev/null || true)
SOURCE_DISK=${SOURCE_DISK:-unknown}
OUTPUT_ROOT="$REPO_ROOT/out/benchmarks/linux-ryzen-5350ge/$RUN_ID"
mkdir -p "$OUTPUT_ROOT"

{
  date -u
  uname -a
  cat /etc/os-release
  lscpu
  free -h
  lsblk -o NAME,MODEL,SIZE,TYPE,MOUNTPOINTS
  findmnt -T "$HANDOFF_ROOT"
  printf 'cpu_governor=%s\n' "$CPU_GOVERNOR"
  printf 'energy_performance_preference=%s\n' "$ENERGY_PREFERENCE"
  df -h "$HANDOFF_ROOT" "$REPO_ROOT"
  uv --version
  uv run python --version
  uv run python -c 'import duckdb, zstandard; print(duckdb.__version__, zstandard.__version__)'
  git rev-parse HEAD
  git status --short
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
    --metadata protection=none \
    --metadata power_source=ac_desktop \
    --metadata "power_governor=$CPU_GOVERNOR" \
    --metadata "energy_performance_preference=$ENERGY_PREFERENCE" \
    --metadata "source_filesystem=$SOURCE_FILESYSTEM" \
    --metadata "source_disk=$SOURCE_DISK" \
    --metadata "output_disk=$SOURCE_DISK"
}

run_case \
  RC_2026-05 \
  "$HANDOFF_ROOT/data/comments/RC_2026-05.zst" \
  b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  4000000
run_case \
  RC_2026-05 \
  "$HANDOFF_ROOT/data/comments/RC_2026-05.zst" \
  b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  6000000
run_case \
  RS_2026-05 \
  "$HANDOFF_ROOT/data/submissions/RS_2026-05.zst" \
  83f25791b1d7cf8663a5c5cea46d291924adf2e00ba02b450c564711725ffcef \
  500000
run_case \
  RS_2026-05 \
  "$HANDOFF_ROOT/data/submissions/RS_2026-05.zst" \
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

HOST_NAME=$(hostname -s)
RESULT_DEST="$HANDOFF_ROOT/results/$HOST_NAME/$RUN_ID"
mkdir -p "$RESULT_DEST"
cp -a "$OUTPUT_ROOT/." "$RESULT_DEST/"
sync

echo "Validated results copied to $RESULT_DEST"
