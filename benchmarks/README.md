# Reproducible conversion benchmark

## Full-archive Linux chunk and merge benchmark

`python -m benchmarks.full_pipeline` reads an entire archive once using the
production `ThreadedZstdReader` and `BinaryLineChunker`, creates retained sorted
chunks, then globally sorts all chunks into one Parquet output. It uses DuckDB's
Python API rather than the fleet/transfer loop or a new CLI process per chunk.
The full benchmark deliberately performs the merge requested for this experiment;
it does not change the recommendation to retain chunks for normal mixed workloads.

```bash
uv run --locked python -m benchmarks.full_pipeline \
  --source /path/to/RC_2026-05.zst \
  --expected-source-sha256 b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc \
  --output-dir out/full-pipeline/may/RC_2026-05 \
  --chunk-rows 6000000 --threads 4 --memory-gb 24 --merge-memory-gb 16
```

For submissions, use `RS_2026-05.zst`, its canonical source hash, and
`--chunk-rows 1500000`. `--stage chunks` and `--stage merge` run the phases
separately. Repeating the same command resumes published chunks; resuming
decompression must still scan the preceding compressed stream once. Successful
chunks and final outputs are atomically published. Sources and retained chunks
are never deleted. Do not modify retained chunks between invocations.

The Linux profile uses four physical cores, 24 decimal GB for chunk conversion,
16 GB for the much larger merge, and local NVMe scratch capped at 75% of currently
free space. The lower merge memory limit leaves headroom for allocations outside
DuckDB's buffer manager and other applications. Full-May measurements showed
that a 20 GB merge setting still caused substantial OS swapping; 16 GB reduced
peak RSS and retained at least 4.4 GiB of available memory on this host. Comments
merged faster at 16 GB; submissions were about 4% slower but had much better
memory headroom. No system power, antivirus, or filesystem settings are changed.

Every chunk uses the master column order, normalizes values with the existing
benchmark helper, pads missing canonical columns, sorts by
`author, subreddit, created_utc`, and writes ZSTD Parquet with a 100,000-row-group
request. Raw JSON types are inferred per chunk, as in the production engine, to
accommodate schema changes through the month. Canonical output schema equality
is enforced before merging without `union_by_name` schema reconciliation.
The source hash, source line count, per-column non-null counts, full-row XOR and
sum fingerprints, schema, and physical ordering are checked. Physical ordering
uses bounded Arrow batches rather than a month-wide window query.

Artifacts include `state.json`, final `result.json`, per-query DuckDB JSON
profiles, per-phase `phases.jsonl`, and one-second `telemetry.jsonl` samples.
Metrics separate staging, conversion, merge, and validation time, and record
process CPU time, RSS, I/O counters, sampled scratch high-water mark, available
memory, swap, load, and disk space. I/O counters are process counters, not NVMe
hardware bytes; sampled peaks can miss sub-second spikes. Full runs are single
observations, not repeated medians.

After chunk creation, `python -m benchmarks.merge_tuning --dataset-dir <dir>`
compares the old single-thread merge against four threads on the first four
chunks, in order 1,4,4,1, with matching 20 GB memory and output settings.
These subset timings are not substitutes for the complete month merge.
The validated `lin-omarchy` profile is tracked in
[`docs/benchmarks/RC_RS_2026-05-omarchy-full-pipeline.json`](../docs/benchmarks/RC_RS_2026-05-omarchy-full-pipeline.json).
The matched chunk-only comparison against `lin-big-omarchy` is tracked in
[`docs/benchmarks/RC_RS_2026-05-lin-big-omarchy-chunking.json`](../docs/benchmarks/RC_RS_2026-05-lin-big-omarchy-chunking.json).
The matched four-versus-six-thread 6M RC comparison is tracked in
[`docs/benchmarks/RC_2026-05-lin-big-omarchy-thread-sweep.json`](../docs/benchmarks/RC_2026-05-lin-big-omarchy-thread-sweep.json).
The optimized Apple M5 Max sweep and complete full-month profile are tracked in
[`docs/benchmarks/RC_RS_2026-05-m5-max-profile.json`](../docs/benchmarks/RC_RS_2026-05-m5-max-profile.json).

### Merge optimization rationale

[DuckDB's redesigned sort](https://duckdb.org/2025/09/24/sorting-again)
already uses parallel streaming k-way merging and adapts to pre-sorted runs.
Pre-sorted Parquet inputs still require ingestion and a global sort; there is no
assumption that chronological chunks have disjoint author ranges. A single
native `COPY (SELECT ... ORDER BY ...)` avoids application-side per-row heaps,
pairwise merge passes, and materializing the entire result in Python.

[Disabling insertion-order preservation](https://duckdb.org/docs/current/guides/performance/how_to_tune_workloads)
reduces unnecessary ordering constraints without removing explicit `ORDER BY`.
Keeping [moderate row groups](https://duckdb.org/docs/current/data/parquet/tips)
preserves scan parallelism and bounds writer buffers. `PER_THREAD_OUTPUT` is not
used because this experiment requires a single globally sorted file. Simple
concatenation cannot preserve global order when author ranges overlap.

`benchmarks.conversion` measures the two conversion paths used in the
cross-platform RC benchmark:

- `staged`: a bounded background decoder copies a row-bounded JSONL chunk,
  then DuckDB normalizes, sorts, and writes Parquet.
- `direct`: DuckDB reads the `.zst` source itself and writes the same normalized,
  sorted rows in one query.

Every invocation records the source SHA-256, hardware, OS, Python, DuckDB,
zstd, Git commit, settings, warm-ups, individual timings, and medians. It
rejects mismatched row counts, schemas, full-row fingerprints, or physical
sort order. Generated data belongs under ignored `out/`; only compact,
reviewed results should be copied into `docs/benchmarks/`.

For the portable M5 Max 128 GB handoff procedure, including the one-command
May RC/RS benchmark suite and internal-NVMe staging, see
[`M5_MAX_128GB.md`](M5_MAX_128GB.md).

## Setup

Install the project and development dependencies:

```bash
uv sync --all-groups
```

The default `python-threaded` decoder uses the project's `zstandard`
dependency. Install the `zstd` executable separately only when benchmarking
the legacy `system` or `text-lines` path. For the canonical `RC_2026-05.zst`
comparison, verify that the source is exactly:

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
  --decoder python-threaded \
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
  --decoder python-threaded `
  --rows 4000000 `
  --repetitions 3 `
  --warmups 0 `
  --threads 4 `
  --memory-limit-gb 25 `
  --metadata protection=enabled `
  --metadata power_plan=Balanced
```

To reproduce the earlier subprocess results, use `--decoder system`. If
`zstd` is not on `PATH`, also pass its complete path with `--zstd`.

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
--copy-mode text-lines --decoder system`. To collect staged and direct results
in one run, use `--mode both`; their execution order alternates between
repetitions.

Direct ingestion is a one-pass comparison, not the production chunking design.
Using repeated `LIMIT/OFFSET` queries would decompress the source for every
chunk.

## Comparing machines

Compare `result.json` files only when all of these match:

- source SHA-256 and row count
- decoder, DuckDB version, schema, codec, sort keys, and memory limit
- warm-up and repetition counts
- protection/power metadata and absence of competing workloads
- `validation.content_fingerprint` and `physical_sort_order`

The benchmark deletes large JSONL and Parquet artifacts after validation.
Pass `--keep-artifacts` only when the outputs are needed for investigation.
