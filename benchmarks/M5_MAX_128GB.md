# M5 Max 128 GB benchmark handoff

This handoff measures the production-style threaded converter on an M5 Max
using the canonical May 2026 Reddit comment and submission archives. The suite
runs three validated repetitions of each workload:

| Dataset | Rows | Purpose |
|---|---:|---|
| `RC_2026-05` | 4,000,000 | Cross-platform canonical comparison |
| `RC_2026-05` | 6,000,000 | Balanced production comment chunk |
| `RS_2026-05` | 500,000 | Lower-memory submission baseline |
| `RS_2026-05` | 1,500,000 | Balanced 25 GB submission chunk |

The source, temporary JSONL, DuckDB scratch directory, and Parquet output are
copied to and run from the M5 Max's internal NVMe. Do not benchmark directly
from the USB disk or modify the source archives on it.

## Handoff layout

```text
reddit-zst-benchmark/
├── COMMIT
├── RUN_M5_MAX_SUITE.sh
├── SHA256SUMS
├── START_HERE.md
├── data/
│   ├── comments/
│   │   └── RC_2026-05.zst
│   └── submissions/
│       └── RS_2026-05.zst
├── reddit-zst-to-parquet.bundle
├── reddit-zst-to-parquet-<commit>.tar.gz
└── results/
```

The canonical sources are:

| File | Bytes | SHA-256 |
|---|---:|---|
| `RC_2026-05.zst` | 47,844,685,635 | `b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc` |
| `RS_2026-05.zst` | 23,823,383,434 | `83f25791b1d7cf8663a5c5cea46d291924adf2e00ba02b450c564711725ffcef` |

## Run the suite

Connect the M5 Max to AC power, close CPU- and disk-intensive applications,
mount the USB disk, and run:

```bash
cd "/Volumes/<USB volume>/reddit-zst-benchmark"
bash RUN_M5_MAX_SUITE.sh
```

The runner:

1. Verifies every file listed in `SHA256SUMS`.
2. Copies and re-verifies both archives on the internal NVMe.
3. Clones the offline Git bundle at the exact commit recorded in `COMMIT`.
4. Installs `uv` if needed, syncs dependencies, and runs the local precheck.
5. Captures hardware, power, protection, package, and Git metadata.
6. Runs and validates all four benchmark cases.
7. Copies the compact result records back under `results/<host>/<UTC run ID>/`.

The default profile uses 12 DuckDB threads, a 25 GB memory limit, the bounded
`python-threaded` decoder, and three repetitions. It does not retain the
multi-gigabyte temporary JSONL or Parquet artifacts.

The defaults can be overridden explicitly:

```bash
THREADS=12 MEMORY_LIMIT_GB=25 REPETITIONS=3 \
  bash RUN_M5_MAX_SUITE.sh
```

If Microsoft Defender is installed, the runner records its health output and
sets `protection=installed`. To record a more specific observed state, set
`PROTECTION=enabled` or `PROTECTION=disabled` without changing the protection
configuration.

## Locations and reruns

Internal sources are stored under:

```text
~/benchmark-data/reddit-zst-benchmark/data/
```

The offline clone is stored at:

```text
~/src/reddit-zst-to-parquet-m5-benchmark
```

Each run uses a new UTC identifier, so rerunning the script preserves earlier
results. Override `RUN_ID` only when resuming a deliberately named run. The
runner refuses a dirty or wrong-commit repository instead of silently using
different code.

After results are safely copied to the USB disk, the internal source copy may
be removed. Never remove files from the USB handoff unless explicitly
requested.
