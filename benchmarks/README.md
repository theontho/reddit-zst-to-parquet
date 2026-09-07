# Reproducible conversion benchmark

`benchmarks.conversion` measures the two conversion paths used in the
cross-platform RC benchmark:

- `staged`: the production-style system `zstd` decoder copies a row-bounded
  JSONL chunk, then DuckDB normalizes, sorts, and writes Parquet.
- `direct`: DuckDB reads the `.zst` source itself and writes the same normalized,
  sorted rows in one query.

Every invocation records the source SHA-256, hardware, OS, Python, DuckDB,
zstd, Git commit, settings, warm-ups, individual timings, and medians. It
rejects mismatched row counts, schemas, full-row fingerprints, or physical
sort order. Generated data belongs under ignored `out/`; only compact,
reviewed results should be copied into `docs/benchmarks/`.

## Setup

Install the project and development dependencies:

```bash
uv sync --all-groups
```

Install `zstd` separately and ensure it is on `PATH`. For the canonical
`RC_2026-05.zst` comparison, verify that the source is exactly:

```text
bytes:  47844685635
sha256: b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc
```

The benchmark computes SHA-256 before timing and fails when
`--expected-source-sha256` does not match.

## Canonical staged benchmark

Use the same row count, memory limit, source, and protection state on every
machine. Set threads to the number of physical cores selected for the
comparison.

macOS/Linux:

```bash
uv run python -m benchmarks.conversion \
  --source /path/to/RC_2026-05.zst \
  --expected-source-sha256 b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  --output-dir out/benchmarks/RC_2026-05/staged-$(hostname) \
  --mode staged \
  --copy-mode block \
  --rows 4000000 \
  --repetitions 3 \
  --warmups 0 \
  --threads 9 \
  --memory-limit-gb 25 \
  --metadata protection=enabled
```

Windows PowerShell:

```powershell
uv run python -m benchmarks.conversion `
  --source 'D:\path\to\RC_2026-05.zst' `
  --expected-source-sha256 b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc `
  --output-dir 'out\benchmarks\RC_2026-05\staged-windows' `
  --mode staged `
  --copy-mode block `
  --rows 4000000 `
  --repetitions 3 `
  --warmups 0 `
  --threads 4 `
  --memory-limit-gb 25 `
  --metadata protection=enabled `
  --metadata power_plan=Balanced
```

If `zstd` is not on `PATH`, pass its complete path with `--zstd`.

## Direct DuckDB and text-line comparisons

Change only the output directory and mode:

```bash
uv run python -m benchmarks.conversion \
  --source /path/to/RC_2026-05.zst \
  --expected-source-sha256 b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  --output-dir out/benchmarks/RC_2026-05/direct-$(hostname) \
  --mode direct --rows 4000000 --repetitions 3 --warmups 0 \
  --threads 9 --memory-limit-gb 25 --metadata protection=enabled
```

To measure the former Python text-line staging path, use `--mode staged
--copy-mode text-lines`. To collect staged and direct results in one run, use
`--mode both`; their execution order alternates between repetitions.

Direct ingestion is a one-pass comparison, not the production chunking design.
Using repeated `LIMIT/OFFSET` queries would decompress the source for every
chunk.

## Comparing machines

Compare `result.json` files only when all of these match:

- source SHA-256 and row count
- DuckDB version, schema, codec, sort keys, and memory limit
- warm-up and repetition counts
- protection/power metadata and absence of competing workloads
- `validation.content_fingerprint` and `physical_sort_order`

The benchmark deletes large JSONL and Parquet artifacts after validation.
Pass `--keep-artifacts` only when the outputs are needed for investigation.
