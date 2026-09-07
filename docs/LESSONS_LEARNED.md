# Conversion Optimization Lessons

This guide turns the measured conversion and query benchmarks into operating
defaults for converting large Reddit Zstandard JSON dumps to Parquet. Exact
measurements and methodology are in [BENCHMARKS.md](BENCHMARKS.md); compact raw
results are in [`docs/benchmarks/`](benchmarks/).

## Recommended target pipeline

For the tested author, subreddit, and date workloads:

1. Read the Zstandard NDJSON directly with DuckDB using a fixed schema.
2. Process the source once in chronological order.
3. Form bounded chunks using the record-type and memory-aware defaults:
   **6,000,000 RC rows** or **1,500,000 RS rows** on the tested 32 GiB profile.
4. Sort each chunk by `author, subreddit, created_utc`.
5. Write each sorted chunk directly to ZSTD-compressed Parquet.
6. Record each file's row count, byte size, and exact `created_utc` range.
7. Validate every chunk before removing its source or intermediate state.
8. Do not globally merge the chunks by default.

The source must be streamed once. Reopening a large compressed source with
successive `OFFSET` queries would repeatedly decompress earlier rows and erase
the direct-ingestion advantage.

These are target architecture recommendations. Confirm that the active engine
implements them before assuming a production run uses this layout.

## Why this pipeline

### Prefer direct DuckDB ingestion

On the same four-million-row sample:

| Pipeline | Time |
|---|---:|
| Python-buffered JSONL, then sorted Parquet | 30.65 s |
| Direct DuckDB ZSTD to sorted Parquet | **23.42 s** |
| Direct DuckDB ZSTD to unsorted Parquet, then Parquet sort | 32.30 s |

Direct sorted ingestion was 23.6% faster than the Python-buffered sorted path.
Sorting during ingestion was 27.5% faster than writing unsorted Parquet and
sorting it in a second pass.

The direct sorted output was also 10.85% smaller than the direct unsorted
output. Sorting improves Parquet compression enough that the sorted path was
faster as well as smaller.

Do not collect millions of decoded JSON lines in a Python `list[str]`. Python
buffering became progressively slower as chunk size increased and raised the
process working set. Keep row parsing, normalization, sorting, and Parquet
encoding inside DuckDB.

When an intermediate JSONL chunk is still required for resumability, keep the
Zstandard subprocess in binary mode and copy large blocks rather than decoding
and re-encoding one line at a time. On the tested Windows node, staging 4M rows
fell from 33.10 seconds with Python text-line iteration to 12.24 seconds with
8 MiB binary blocks. The full staged conversion fell to 34.48 seconds per 4M
rows, 3.9x faster than the historical 146-second average.

Do not disable Defender to optimize this conversion. With real-time, behavior,
IOAV, and script scanning disabled, the same Windows benchmark took a median
35.79 seconds versus 34.48 seconds with protection enabled. The structural
binary-streaming change, not antivirus configuration, produced the speedup.

DuckDB 1.5.2 successfully read the tested long-range-compressed Zstandard file
directly. Older DuckDB versions previously required the system `zstd` decoder
and a FIFO, so direct decoding must remain covered by a real conversion smoke
test when DuckDB changes.

### Sort bounded chunks, not the whole month

The source is chronological. Keeping chronological file boundaries while
sorting rows inside each file provides two useful levels of organization:

- `author, subreddit, created_utc` ordering inside each file supports row-group
  pruning for author-oriented queries.
- Non-overlapping chronological file ranges support file pruning for date
  queries.

A global author-first sort discards the second property. It marginally helps
some broad author and subreddit scans but makes date filtering dramatically
worse and adds a large, failure-prone merge phase.

The unsorted-chunk global merge exhausted a 329.4 GiB spill allowance. The
successful historical merge used a 1 TB allowance and did not record its peak.
Neither result supports a month-wide merge as the normal output path.

If a global merge is explicitly required, sorting intermediate chunks first
does not eliminate the final global sort. Avoid sorting twice unless the
sorted chunks are themselves a required retained artifact.

## Choosing a comment chunk size

Direct sorted conversion throughput was effectively flat between 4M and 8M
rows. Larger chunks did not make conversion faster; they reduced file count at
the cost of a larger working set, more spill on constrained systems, and a
larger retry unit.

| Rows/file | Files for 349.6M rows | Average file | Primary use |
|---:|---:|---:|---|
| 2M | 175 | 297 MiB | Date-dominated queries |
| 4M | 88 | 581 MiB | Conservative memory and retry size |
| **6M** | **59** | **858 MiB** | **Balanced default** |
| 8M | 44 | 1,141 MiB | Author and count-heavy queries on 32 GiB+ hosts |

Use **6M** unless the workload strongly favors another size:

- Use **2M** when date-range retrieval dominates.
- Use **4M** when scratch space, memory, or retry cost is constrained.
- Use **8M** when standalone author lookup and count-only subreddit queries
  dominate and the host has enough memory.
- Do not choose 7M or 8M to improve conversion throughput; no such gain was
  measured.

The chunked engine selects a spill-avoiding size automatically. It first caps
the configured DuckDB limit to the host RAM budget, then allocates 250,000
comment rows per effective GB, rounds down to 250,000 rows, and caps the result
at 6M.

| Host/profile | Effective DuckDB memory | Automatic RC chunk |
|---|---:|---:|
| 16 GiB, default 80% RAM budget | 12 GB | 3M |
| 16 GiB, explicit low-memory profile | 8 GB | 2M |
| 32 GiB, default 80% RAM budget | 25 GB | 6M |

Submission records use a separately measured curve because they are much
wider. The tested RS sample produced about 620 bytes of Parquet per row, versus
about 150 bytes for RC. The RS calculation reserves 5 GB for its fixed working
set, allocates 100,000 rows per remaining GB, rounds down to 250,000 rows, and
caps the result at 1.5M.

| Host/profile | Effective DuckDB memory | Automatic RS chunk | Measured spill |
|---|---:|---:|---:|
| 16 GiB, explicit low-memory profile | 8 GB | 250k | 0 |
| 16 GiB, default 80% RAM budget | 12 GB | 500k | 0 |
| 32 GiB, default 80% RAM budget | 25 GB | 1.5M | 0 |

At 25 GB, the 500k, 750k, and 1.5M RS sizes all sustained about 41,000 rows/s.
The 1.5M default produced an 886 MiB file without spill, balancing file count
against memory and retry cost. A 3M chunk spilled 1.28 GiB and was 20.7% slower
per row. At 8 GB and 12 GB, increasing one step beyond the selected default
caused measurable spill.

DuckDB threads are capped at the lower of the configured value, physical CPU
cores, and one thread per 2 GB of effective memory. Hyperthreads did not improve
the measured Windows conversion: four threads completed a 4M chunk in 34.48
seconds, versus 37.51 seconds at seven threads and 34.88 seconds at eight. An
explicit `--chunk-size` overrides automatic sizing and logs a warning when it
exceeds the spill-avoiding estimate. Set `pipeline.adaptive_chunk_size = false`
to use the configured `pipeline.chunk_size` instead.

## Query-layout lessons

The file-size query benchmark compared identical 349,577,451-row datasets with
the same schema, row-group size, codec, and per-file sort order.

| Query category | Best chunk size | Result relative to global merge |
|---|---:|---|
| Rare author count | 8M | 2.95x faster |
| Rare author full rows | 8M | 1.29x faster |
| Active author full rows | 8M | Effectively tied |
| Author plus subreddit | 6M | 1.42x faster |
| Date count | 2M | 10.83x faster |
| Date full rows | 2M | 17.46x faster |
| Subreddit count | 8M | Best chunk size, but merge was 1.42-4.10x faster |
| Subreddit full rows | 4M-8M | Merge was 1.11-1.76x faster |

The global merge is not necessary for fast author lookup. Its strongest
advantages are fewer files, about 13% lower storage than the 6M layout, and
faster broad subreddit scans. Those benefits do not compensate for lost date
pruning and merge cost for the mixed target workload.

File sizing cannot fix high-cardinality subreddit full-row scans because
`subreddit` is the secondary sort key and matches span many authors. If those
queries require low latency, build a separate derivative dataset sorted by
`subreddit, created_utc, author`. Do not distort the primary author-oriented
layout or add a global merge for that access pattern.

## Memory and spill sizing

DuckDB's `memory_limit` controls its buffer manager, not the process's complete
resident set. Parsing, compression, vectors, strings, and other allocations can
make RSS exceed the configured limit.

The compressed Parquet output size is also not a direct measure of the sort
working set. On the 32 GiB host, a 6M-row RC output of 858 MiB used
13.39-15.1 GiB RSS: roughly **16 times the final compressed file size**. The
1.5M-row RS default produced an 886 MiB file and had a 18.69 GiB median peak
RSS across repeated runs.

### 32 GiB RC profile

Use:

- 6M rows per chunk
- 25 GB DuckDB memory limit
- Nine DuckDB threads on the tested M1 Pro
- A configured local spill directory as a safety mechanism

Measured bounded sorts from 2M through 8M produced **zero spill** with this
profile. The 6M sampled run peaked at 13.39 GiB RSS. This zero-spill result does
not apply to a month-wide global merge.

### 16 GiB RC profile

For the balanced 6M layout with controlled spill, use:

- 6M rows per chunk
- 8 GB DuckDB memory limit
- Four DuckDB threads
- At least 8 GiB of free local scratch space

This profile was simulated on the 32 GiB benchmark host. A physical 16 GiB
machine can differ because of operating-system and concurrent-process memory.

| Rows | Peak RSS | Peak spill | Interpretation |
|---:|---:|---:|---|
| 2M | 6.58 GiB | 0 | Maximum headroom, highest file count |
| 4M | 9.80 GiB | 0.49 GiB | Conservative choice |
| **6M** | **9.60 GiB** | **3.65 GiB** | **Balanced 16 GiB choice** |
| 8M | 10.58 GiB | 6.41 GiB | Works, but unnecessary spill and retry cost |

For a strict no-spill policy, automatic sizing is more conservative: a default
16 GiB host receives a 12 GB DuckDB limit, six threads, and 3M chunks. That
profile completed in 17.73 seconds at 169,171 rows/s with 9.51 GiB peak RSS and
zero measured spill. An explicit 8 GB DuckDB limit selects 2M chunks, also with
zero measured spill.

DuckDB writes out-of-memory sorted runs to `temp_directory`, merges them into
the output, and removes them when the query closes. Spill therefore peaks per
chunk and does not accumulate over a successful sequential conversion.
`max_temp_directory_size` is a ceiling, not preallocated space.

Use fast local SSD storage for source, output, and spill. Size the filesystem
for the final output plus the peak spill allowance; do not count the spill
allowance as permanent dataset storage.

## Schema and data robustness

- Use a fixed schema rather than inferring every chunk independently.
- Normalize historically unstable fields, such as `edited`, before writing.
- Map fields that were historically always null to a future-compatible type
  rather than Arrow's null-only type.
- Preserve uncommon or structurally inconsistent fields in `extra_json`
  instead of expanding the top-level schema indefinitely.
- Serialize object/list values when the canonical column is textual.
- Keep `extra_json` and all-null columns identical across every chunk so
  multi-file reads do not require schema repair.

## Benchmarking lessons

- Preserve insertion order when selecting a source prefix. Parallel `LIMIT`
  without `preserve_insertion_order=true` can select different rows.
- Compare identical row sets, schemas, codecs, sort keys, and row-group sizes.
- Validate counts and an order-independent full-row fingerprint across layouts.
- Check physical sort order, not just query results.
- Use fresh DuckDB connections, warm-ups, repeated timings, and rotated layout
  order to reduce cache and execution-order bias.
- Count-only timings do not represent full-row retrieval. Force all columns to
  be read when measuring scan-heavy workloads.
- File-size layout experiments can be generated Parquet-to-Parquet to isolate
  layout effects, but production conversion speed must be tested from the
  original Zstandard source.
- Record peak process RSS separately from DuckDB's configured memory limit.
- Sample the spill directory during execution because successful queries delete
  their temporary files.
- Treat single-run constrained-memory results as capacity checks, not precise
  throughput rankings.

## Operational safeguards

- Write outputs and spill to fast local storage when possible.
- Keep source chunks until output row count, schema, fingerprint, and sort order
  are verified.
- Publish a manifest containing source hash, engine version, settings, file
  order, row counts, byte sizes, and timestamp ranges.
- Use atomic state transitions for download, conversion, validation, and
  upload.
- Retain enough information to resume at a chunk boundary rather than rerunning
  a month.
- Clean only exact, known temporary paths after successful validation.
- Run at least one real end-to-end conversion after changing ingestion,
  sorting, schema normalization, or output layout.

## Optimization priorities

Apply optimizations in this order:

1. Remove Python row buffering.
2. Replace per-line text transcoding with large binary stream copies.
3. Read the source once with a fixed schema.
4. Sort while writing each bounded chunk.
5. Eliminate the default global merge.
6. Tune chunk size for the actual query workload.
7. Tune memory and threads for the host, allowing spill as a safety valve.
8. Add a derivative physical layout only for a proven secondary access pattern.

This ordering targets structural costs first. Increasing threads, memory, or
chunk size cannot compensate for repeated decompression, Python object
materialization, a redundant Parquet pass, or an unnecessary global sort.
