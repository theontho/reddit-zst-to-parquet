# M5 Max 128 GB benchmark handoff

This runbook is for an agent receiving the portable benchmark disk. It measures
the production-style staged converter at 4M and 6M rows on an M5 Max Mac with
128 GB of memory. The source, temporary JSONL, DuckDB scratch directory, and
Parquet output must all be on the Mac's internal NVMe while timing.

Do not benchmark directly from the USB disk. Do not change the source archives
on that disk.

## Expected handoff layout

```text
reddit-zst-benchmark/
├── START_HERE.md
├── SHA256SUMS
├── data/
│   └── comments/
│       ├── RC_2026-04.zst
│       ├── RC_2026-05.zst
│       └── RC_2026-06.zst
├── reddit-zst-to-parquet.bundle
└── reddit-zst-to-parquet-<commit>.tar.gz
```

`RC_2026-05.zst` is the canonical cross-machine comparison source:

```text
bytes:  47844685635
sha256: b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc
```

## 1. Copy the handoff to internal storage

Replace `<USB volume>` with the mounted volume name:

```bash
export HANDOFF_ROOT="/Volumes/<USB volume>/reddit-zst-benchmark"
export WORK_ROOT="$HOME/benchmark-data/reddit-zst-benchmark"

cd "$HANDOFF_ROOT"
shasum -a 256 -c SHA256SUMS

mkdir -p "$WORK_ROOT/data/comments" "$HOME/src"
rsync -ah --progress \
  "$HANDOFF_ROOT/data/comments/RC_2026-05.zst" \
  "$WORK_ROOT/data/comments/"

cd "$WORK_ROOT"
test "$(stat -f %z data/comments/RC_2026-05.zst)" = "47844685635"
printf '%s  %s\n' \
  b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  data/comments/RC_2026-05.zst | shasum -a 256 -c -
```

Clone the repository bundle onto the internal NVMe. The bundle contains Git
history and makes the handoff independent of network access. The `.tar.gz`
file is a browseable source snapshot but is not needed for the benchmark:

```bash
test ! -e "$HOME/src/reddit-zst-to-parquet"
git clone "$HANDOFF_ROOT/reddit-zst-to-parquet.bundle" \
  "$HOME/src/reddit-zst-to-parquet"
cd "$HOME/src/reddit-zst-to-parquet"
git remote set-url origin https://github.com/theontho/reddit-zst-to-parquet.git
git status --short
```

The status output must be empty. Record `git rev-parse HEAD` with the results.

## 2. Install prerequisites

Install `uv` as the logged-in user, never with `sudo`:

```bash
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
```

Install the system Zstandard decoder if it is absent. Prefer an existing
Homebrew installation:

```bash
if ! command -v zstd >/dev/null 2>&1; then
  brew install zstd
fi

cd "$HOME/src/reddit-zst-to-parquet"
uv sync --all-groups
uv run reddit-zst-to-parquet precheck --method local --skip-connection
uv run python -m benchmarks.conversion --help >/dev/null
```

If Homebrew is not installed, install it through its official procedure before
installing `zstd`. Do not run downloaded setup code with `sudo`.

## 3. Capture the untuned machine state

Connect the Mac to AC power, close CPU- or disk-intensive applications, and
record the state without changing performance settings:

```bash
mkdir -p out/benchmarks/RC_2026-05/m5-max-128gb
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
  df -h "$WORK_ROOT" "$PWD"
  uv --version
  uv run python --version
  uv run python -c 'import duckdb, zstandard; print(duckdb.__version__, zstandard.__version__)'
  zstd --version
  git rev-parse HEAD
  git status --short
} | tee out/benchmarks/RC_2026-05/m5-max-128gb/environment.txt
```

Determine and record the endpoint-protection state. If Microsoft Defender is
installed, use `mdatp health` and leave protection enabled. Otherwise record
`protection=none`. Do not weaken protection for the benchmark.

## 4. Run the canonical 4M and 6M benchmarks

Use 12 DuckDB threads and a 25 GB memory limit. This is the comparable
high-performance profile selected by the converter's physical-core and
one-thread-per-2-GB limits; the extra system memory remains available to macOS
and the filesystem cache.

Set the metadata values to the state observed above:

```bash
export SOURCE="$WORK_ROOT/data/comments/RC_2026-05.zst"
export SOURCE_SHA256=b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc
export PROTECTION=none
export POWER_SOURCE=ac

for ROWS in 4000000 6000000; do
  uv run python -m benchmarks.conversion \
    --source "$SOURCE" \
    --expected-source-sha256 "$SOURCE_SHA256" \
    --output-dir "out/benchmarks/RC_2026-05/m5-max-128gb/staged-${ROWS}" \
    --mode staged \
    --copy-mode block \
    --rows "$ROWS" \
    --repetitions 3 \
    --warmups 0 \
    --threads 12 \
    --memory-limit-gb 25 \
    --metadata "protection=$PROTECTION" \
    --metadata "power_source=$POWER_SOURCE" \
    --metadata source_filesystem=apfs \
    --metadata source_disk=internal_nvme \
    --metadata output_disk=internal_nvme
done
```

Do not use `--keep-artifacts`; the harness removes the multi-gigabyte JSONL and
Parquet files after validation. It retains `result.json` and `raw-schema.json`.

## 5. Validate and return results

Both result files must contain three measured runs. Every run must report its
requested row count, `physical_sort_violations: 0`, and a consistent content
fingerprint. The 4M canonical fingerprint is:

```text
7365298922509737757
```

Validate the result JSON and copy the compact records back to the USB disk:

```bash
python3 -m json.tool \
  out/benchmarks/RC_2026-05/m5-max-128gb/staged-4000000/result.json >/dev/null
python3 -m json.tool \
  out/benchmarks/RC_2026-05/m5-max-128gb/staged-6000000/result.json >/dev/null

export RESULT_DEST="$HANDOFF_ROOT/results/$(scutil --get LocalHostName)"
mkdir -p "$RESULT_DEST"
rsync -ah \
  out/benchmarks/RC_2026-05/m5-max-128gb/ \
  "$RESULT_DEST/"
sync
```

Report the median stage, Parquet, total, and rows-per-second values for both
chunk sizes. Also report the individual totals, Git commit, DuckDB version,
protection state, power source, and whether either run produced sort violations
or validation errors.

After confirming the result copy, the internal working source can be removed.
Never remove files from the USB handoff unless the user explicitly requests it.
