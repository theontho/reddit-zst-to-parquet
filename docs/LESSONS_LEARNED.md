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

When an intermediate JSONL chunk is still required for resumability, decode
and copy large binary blocks rather than decoding and re-encoding one line at
a time. On the tested Windows node, staging 4M rows fell from 33.10 seconds
with Python text-line iteration to 12.24 seconds with 8 MiB binary blocks. The
full staged conversion fell to 34.48 seconds per 4M rows, 3.9x faster than the
historical 146-second average.

The current default removes the subprocess from this path as well. A worker
thread decompresses 8 MiB blocks in process while the main thread counts
newlines and writes JSONL. A two-block queue bounds read-ahead to about 16 MiB
and preserves chunk boundaries, concatenated Zstandard frames, resumability,
and decoder error propagation. The system `zstd` path remains available as an
explicit fallback.

During Ubuntu development, isolated 4M staging took 14.41 seconds through the
original CLI pipe, 13.89 seconds with a larger pipe, 13.84 seconds with
serialized in-process decoding, and 9.53 seconds with the bounded threaded
reader. A single fully validated integrated run took 10.31 seconds to stage
and 27.52 seconds total, or 145,354 rows/s. These were development
measurements rather than a repeated canonical result; the planned 6M repeat
was interrupted by the machine reinstall.

The later three-repetition Omarchy suite validated the committed decoder on
both record types. Median staging was 13.03 seconds for 4M comments, 20.35
seconds for 6M comments, 4.61 seconds for 500k submissions, and 13.19 seconds
for 1.5M submissions. Do not compare those totals directly with Ubuntu:
Omarchy also changed the kernel, Python version, filesystem, transparent
compression, and power governor.

Disabling Defender's real-time scanning did not optimize this conversion. With
real-time, behavior, IOAV, and script scanning disabled, the same Windows
benchmark took a median 35.79 seconds versus 34.48 seconds with protection
enabled. This was not a full engine-disable test: the protected service,
process, and file-system filter remained loaded. The structural binary-streaming
change, not antivirus configuration, produced the measured speedup.

After replacing Windows with Ubuntu on the same Ryzen 3 PRO 5350GE machine,
the matched 4M-row staged conversion took 31.96 seconds, 7.3% less wall time
than Windows. Linux's DuckDB phase was 21.8% faster, but staging from the same
SATA source disk was 18.2% slower.

A controlled follow-up copied the complete source onto the Linux host's native
NVMe and reverified its SHA-256. Staging improved only 0.2%, from 14.46 to
14.43 seconds, proving the NTFS/FUSE source path was not the meaningful
bottleneck. The native-NVMe total was 31.52 seconds, 8.6% faster than Windows.
The OS comparison therefore comes mainly from Linux's 22.8% faster DuckDB
phase, while the remaining staging difference is in decompression or other
platform behavior rather than source storage.

The native-NVMe 6M run sustained 127,888 rows/s versus 126,907 rows/s at 4M,
a 0.8% difference within normal variance. This independently confirms that
larger chunks reduce file count but do not materially improve conversion
throughput.

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

The selected Windows operating profile keeps Defender enabled, uses the
Balanced power plan, stages 8 MiB binary blocks, caps DuckDB at four physical
cores, converts chunks sequentially so zstd does not compete with DuckDB, and
skips the global merge. Changing the power plan, using SMT threads, overlapping
decompression with conversion, and disabling real-time scanning did not improve
the measured Windows throughput.

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

### Full-archive merge profile on a shared 32 GB host

The complete May 2026 RC and RS archives were also merged on the four-core
`lin-omarchy` host. Four merge threads were 1.95x faster than one thread for a
24M-row RC subset and 1.65x faster for a 6M-row RS subset. These are subset
measurements, not full-month single-thread estimates.

For a requested global merge, use a 16 GB DuckDB limit on this shared host.
Compared with 20 GB, the 16 GB profile reduced the RC merge from 37.44 to
33.79 minutes and avoided 12.65 GiB of host swap-out. The RS merge increased
from 21.87 to 22.73 minutes, but avoided 19.93 GiB of swap-out and kept at
least 5.91 GiB available. DuckDB's configured limit is not a process-RSS
ceiling: peak RSS still reached 22.10 GiB for RC and 20.84 GiB for RS.

The selected full processing totals were 82.71 minutes for 349,577,451
comments and 52.59 minutes for 47,453,180 submissions. Chunk conversion did
not spill; the global merges spilled 314.68 and 173.21 GiB logically. These
were single observations on compressed Btrfs, and the 20 GB runs always
preceded the 16 GB runs, so treat 16 GB as a safe machine profile rather than
a universal optimum. Retained sorted chunks remain preferable when a single
globally sorted file is not required.

### Scaling the same chunk profile to a larger host

Keeping the four-thread, 24 GB, 6M RC, and 1.5M RS settings unchanged,
`lin-big-omarchy` completed full-month chunking 1.284x faster for comments and
1.204x faster for submissions than `lin-omarchy`. Wall time fell from 48.92 to
38.10 minutes for RC and from 29.86 to 24.79 minutes for RS. Both machines used
the same Omarchy, kernel, Python, DuckDB, Zstandard, Btrfs compression, source
hashes, and output settings, and all 91 chunks matched by content.

The larger host has a Ryzen 5 5600X and 64 GiB RAM, versus a Ryzen 3 PRO 5350GE
and 32 GiB RAM. Neither run spilled, peak RSS stayed below 19 GiB, and average
CPU use remained below three cores despite a four-thread DuckDB limit. The
observed gain therefore describes this complete pipeline on these hosts; it
does not isolate CPU architecture, cache, memory bandwidth, storage, or
background activity.

A controlled follow-up showed that six threads help the 5600X on 6M comment
chunks. At the same 24 GB limit, the three-run median improved from 166,146 to
175,532 rows/s, a 5.65% gain. Staging was effectively unchanged while the
DuckDB/Parquet phase became 8.22% faster. Six threads are the better of the two
measured settings, but do not treat that as the final optimum until higher
thread counts are tested.

### Scaling to an M5 Max with 128 GiB

The M5 Max required different conversion and merge profiles. RC conversion
peaked at 18M rows, 15 threads, and 80 GB, while RS peaked at 5M rows, all 18
physical cores, and 100 GB. A balanced RC profile uses 16M rows because it
remained within 1% of peak throughput with a smaller retry unit.

Merge resources should be tuned independently from chunk conversion. The
complete RC merge was fastest at 12 threads and 80 GB; RS was fastest at nine
threads and 64 GB. Increasing either workload to a 100 GB merge limit raised
process RSS to roughly 96 GiB, caused system swap growth, and slowed the merge.
Large-memory machines still need headroom outside DuckDB's buffer manager.

Complete conversion work plus the median winning merge took 14.87 minutes for
349.6M comments and 10.53 minutes for 47.5M submissions. This was roughly
5.57x and 5.00x faster than the corresponding `lin-omarchy` totals, but the
hosts used intentionally different resource profiles. Treat these as
machine-level capacity results, not per-core architecture comparisons.

The April-July production run added an important disk-boundary lesson. RC
July's 371.2 million rows could not complete as one global sort when DuckDB's
temporary directory was capped at 316.55 GiB, even with 128 GiB of unified
memory and an 80 GB DuckDB memory limit. The previously validated chunk files
remained useful: the merge was recovered by partitioning the complete sort
keyspace into 17 disjoint author ranges, sorting each range independently, and
concatenating them in key order.

For future full-month merges:

1. Keep validated chunk Parquet files until the final output has passed
   row-count, fingerprint, physical-order, and SHA-256 validation.
2. Prefer the single global merge while its estimated scratch requirement fits
   comfortably within available disk.
3. When the global merge is disk-bound, switch to disjoint leading-key ranges
   that include a dedicated null range, then concatenate the sorted range
   outputs in key order.
4. Budget storage for DuckDB spill, retained range outputs, and the growing
   final output independently. DuckDB's reported spill alone is not total
   scratch usage.

This recovery trades throughput for a bounded failure domain and preserves
global `author, subreddit, created_utc` order without restarting the expensive
chunk conversion.

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
