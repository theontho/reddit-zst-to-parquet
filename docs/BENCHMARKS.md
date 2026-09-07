# Conversion Benchmarks

This document records conversion measurements used to choose the chunking and
sorting strategy. Generated Parquet files and full logs are intentionally not
tracked; the compact raw results are in
[`docs/benchmarks/RC_2026-05-chunk-sizing.json`](benchmarks/RC_2026-05-chunk-sizing.json)
and
[`docs/benchmarks/RS_2026-05-chunk-sizing.json`](benchmarks/RS_2026-05-chunk-sizing.json).

## September 2026 chunk-sizing benchmark

### Test environment

| Setting | Value |
|---|---|
| Source | `RC_2026-05.zst` |
| Source SHA-256 | `b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc` |
| Machine | Apple M1 Pro MacBook, 32 GiB unified memory |
| DuckDB | 1.5.2 |
| Zstandard | 1.5.7 |
| DuckDB threads | 9 |
| DuckDB memory limit | 25 GB |
| Parquet compression | ZSTD |
| Sort order | `author, subreddit, created_utc` |

Every test used the first requested number of rows in source order. Direct
DuckDB tests read and decompressed the `.zst` file without Python handling
individual rows. Python-buffered tests reproduced the baseline path:

1. Decode rows with the `zstd` process.
2. Retain all rows for the chunk in a Python `list[str]`.
3. Write a temporary JSONL file.
4. Read, normalize, and write the JSONL with DuckDB.

Source-order preservation was enabled so every path received identical rows.
The raw schema was discovered once and excluded from direct-ingestion timing.
Each output was checked for its expected row count, matching schema and content
fingerprint, and correct physical order when sorting was requested.

### Four-million-row conversion matrix

The table reports the median of three repetitions.

| Input path | Unsorted Parquet | Sorted Parquet |
|---|---:|---:|
| Python-buffered JSONL | 26.29 s | 30.65 s |
| Direct DuckDB ZSTD ingestion | 28.46 s | **23.42 s** |

Sorting the direct unsorted Parquet into a second Parquet file took **3.84
seconds**, making the two-step direct path **32.30 seconds** in total. Sorting
during direct ingestion was therefore 27.5% faster than writing and sorting in
separate operations.

The sorted direct output was 601,763,427 bytes versus 674,975,210 bytes for the
unsorted output, a 10.85% reduction. Better compression made direct sorted
output faster than direct unsorted output despite the sort work.

### Scaling from two to eight million rows

The table reports the median of three complete matrix repetitions per size.

| Rows | Python unsorted | Python sorted | Direct unsorted | Direct sorted | Parquet-to-Parquet sort |
|---:|---:|---:|---:|---:|---:|
| 2M | 12.73 s | 14.85 s | 14.38 s | **11.41 s** | 1.99 s |
| 4M | 26.29 s | 30.65 s | 28.46 s | **23.42 s** | 3.84 s |
| 8M | 68.27 s | 84.47 s | 55.81 s | **47.23 s** | 13.76 s |

Direct sorted ingestion sustained 175,275, 170,760, and 169,380 rows per
second at 2M, 4M, and 8M rows respectively. Python-buffered sorted throughput
fell from 134,691 rows per second at 2M to 94,711 rows per second at 8M.

Peak RSS for each complete five-case matrix rose from 15.1 GB at 2M to 17.7 GB
at 4M and 24.0 GB at 8M. No run swapped. The result shows that the 8M
Python-buffered path suffers memory pressure, while direct DuckDB ingestion
remains close to linear.

### Direct sorted chunk-size sweep

The focused sweep reran direct sorted ingestion at every whole-million size
from 4M through 8M. Execution order was varied to reduce cache and thermal
bias. The 4M, 5M, and 6M cases used five repetitions; 7M and 8M used three.

| Rows | Median time | Median throughput | Output size | Median peak RSS |
|---:|---:|---:|---:|---:|
| 4M | 23.07 s | 173,401 rows/s | 574 MiB | 12.4 GiB |
| 5M | 28.94 s | 172,754 rows/s | 716 MiB | 13.6 GiB |
| **6M** | **34.60 s** | **173,414 rows/s** | **858 MiB** | **15.1 GiB** |
| 7M | 40.85 s | 171,371 rows/s | 997 MiB | 16.3 GiB |
| 8M | 46.23 s | 173,047 rows/s | 1,135 MiB | 17.7 GiB |

Throughput is effectively flat from 4M through 8M. The difference between the
4M and 6M medians is 0.007%, well below run-to-run noise. Larger chunks do not
make the conversion faster; they only reduce file count while increasing the
sort working set and failure cost.

### Spill on the 32 GiB host

A follow-up run sampled DuckDB's temporary directory every 10 ms using the
original 25 GB memory limit and nine threads. None of the bounded sorts spilled
to disk.

| Rows | Time | Peak process RSS | Peak spill |
|---:|---:|---:|---:|
| 2M | 11.75 s | 8.24 GiB | **0** |
| 4M | 23.00 s | 12.11 GiB | **0** |
| **6M** | **37.62 s** | **13.39 GiB** | **0** |
| 8M | 49.80 s | 15.56 GiB | **0** |

This applies to independently bounded chunk sorts. It does not apply to the
month-wide global merge, whose working set is much larger and can consume
hundreds of GiB of spill.

### Constrained-memory check

Direct sorted conversion was also run with four threads and lower DuckDB
memory limits to approximate a 16 GiB host. A 10 ms sampler measured the
temporary directory's high-water mark before DuckDB deleted its spill files.
These are single-run smoke tests on the same 32 GiB Mac, not measurements from
a physical 16 GiB machine.

| DuckDB limit | Rows | Time | Peak process RSS | Peak spill | Result |
|---:|---:|---:|---:|---:|---|
| 8 GB | 2M | 11.82 s | 6.58 GiB | 0 | Passed |
| 8 GB | 4M | 23.65 s | 9.80 GiB | 0.49 GiB | Passed |
| **8 GB** | **6M** | **38.59 s** | **9.60 GiB** | **3.65 GiB** | **Passed** |
| 8 GB | 8M | 52.76 s | 10.58 GiB | 6.41 GiB | Passed |
| 10 GB | 6M | 44.52 s | 11.41 GiB | 1.14 GiB | Passed |
| **12 GB** | **3M** | **17.73 s** | **9.51 GiB** | **0** | **Passed** |

All outputs had the expected row counts, content fingerprints, and physical
sort order. DuckDB's `memory_limit` does not cover every allocation, so process
RSS can exceed the configured limit. During `ORDER BY`, DuckDB writes sorted
runs that do not fit in its memory budget to `temp_directory`, merges them into
the Parquet output, and removes the temporary files when the query closes.
Spill therefore peaks per chunk rather than accumulating across sequential
chunks.

On a 16 GiB host, use an 8 GB DuckDB limit, four threads, and allow at least
8 GiB of free spill space for 6M chunks. The measured 6M run was 11.5% slower
than the unconstrained nine-thread median while leaving about 6 GiB for the
operating system. A 4M chunk is the safer low-disk option; 8M completed but
roughly doubled 6M's spill and left less memory headroom.

For automatic no-spill sizing, the engine budgets 250,000 comment rows per
effective DuckDB memory GB and caps chunks at 6M. Thus a default 16 GiB host
uses 12 GB, six threads, and 3M rows; the measured profile above did not spill.
The 32 GiB profile uses 25 GB, nine threads, and 6M rows. Explicit
`--chunk-size` values remain available for workloads willing to trade spill
space for fewer files.

### Decision

Use **six million rows per bounded direct-DuckDB sorted chunk** when optimizing
the balance of file size and file count:

- It preserves peak observed throughput.
- Its approximately 858 MiB output remains below the 1 GiB target.
- A 349.6-million-row month requires about 59 chunks instead of 88 at 4M.
- It retains substantial memory headroom on a 32 GiB machine.

Use **four million rows** on memory-constrained machines that cannot reserve
approximately 10 GiB of process memory, or where smaller retry units matter
more than reducing file count. Do not use 7M or 8M for speed: neither produced
a measurable throughput gain.

These measurements apply to independently bounded sorts. They do not establish
that a month-wide global sort is safe or efficient.

## Submission-record chunk sizing

The same direct-DuckDB methodology was applied to `RS_2026-05.zst`, a
23,823,383,434-byte compressed submission archive with SHA-256
`83f25791b1d7cf8663a5c5cea46d291924adf2e00ba02b450c564711725ffcef`.
The source expands to approximately 235 GiB. A fixed 156-column raw schema was
inferred once and excluded from timing; outputs were normalized against the RS
master schema and sorted by `author, subreddit, created_utc`.

Submission rows are substantially wider than comment rows. The first 500,000
RS rows produced a 293 MiB Parquet file, compared with approximately 72 MiB per
500,000 RC rows at the selected RC sizes. RS chunk limits therefore need their
own memory curve rather than a constant fraction of the RC limit.

### 25 GB high-memory sweep

The 500k, 750k, and 1.5M points report medians of three runs. Other points are
single-run boundary checks.

| Rows | Time | Throughput | Output size | Peak RSS | Peak spill |
|---:|---:|---:|---:|---:|---:|
| 250k | 6.66 s | 37,541 rows/s | 149 MiB | 4.24 GiB | 0 |
| 500k | 12.11 s | 41,277 rows/s | 293 MiB | 8.34 GiB | 0 |
| 750k | 18.25 s | 41,103 rows/s | 442 MiB | 12.08 GiB | 0 |
| 1M | 27.62 s | 36,211 rows/s | 591 MiB | 12.08 GiB | 0 |
| **1.5M** | **35.91 s** | **41,768 rows/s** | **886 MiB** | **18.69 GiB** | **0** |
| 2M | 56.76 s | 35,234 rows/s | 1,172 MiB | 16.69 GiB | 0 |
| 3M | 90.52 s | 33,143 rows/s | 1,750 MiB | 18.22 GiB | 1.28 GiB |

Throughput at 500k, 750k, and 1.5M was effectively flat after warm-up. The
1.5M size is the balanced 32 GiB default: it retains the throughput plateau,
keeps output below 1 GiB, and remained spill-free. Although the single 2M run
also did not spill, it was 15.6% slower per row and produced a 1.14 GiB file.
The 3M run spilled and was 20.7% slower per row than the 1.5M median.

### Constrained-memory sweep

| DuckDB limit | Threads | Rows | Time | Peak RSS | Peak spill |
|---:|---:|---:|---:|---:|---:|
| **8 GB** | **4** | **250k** | **6.67 s** | **4.31 GiB** | **0** |
| 8 GB | 4 | 500k | 12.84 s | 8.56 GiB | 653 MiB |
| 8 GB | 4 | 750k | 19.47 s | 10.66 GiB | 1.72 GiB |
| 8 GB | 4 | 1M | 27.28 s | 11.62 GiB | 2.86 GiB |
| **12 GB** | **6** | **500k** | **12.24 s** | **8.22 GiB** | **0** |
| 12 GB | 6 | 750k | 18.70 s | 11.53 GiB | 745 MiB |
| 12 GB | 6 | 1M | 24.12 s | 14.36 GiB | 1.70 GiB |
| 12 GB | 6 | 1.5M | 41.65 s | 16.37 GiB | 4.34 GiB |

The selected 250k/8 GB and 500k/12 GB points were repeated three times and
remained spill-free. The next 250k increment spilled in both profiles. This
supports a submission-specific adaptive curve that reserves 5 GB for fixed
working-set cost, budgets 100,000 rows per remaining GB, rounds down to 250k,
and caps at 1.5M:

```text
rows = clamp(round_down((effective_memory_gb - 5) * 100,000, 250,000),
             250,000, 1,500,000)
```

That formula selects 250k rows at 8 GB, 500k at 12 GB, and 1.5M at 25 GB.
These are deliberately no-spill defaults; explicit chunk sizes can still trade
scratch I/O and retry cost for fewer files.

## Sorted chunk dataset versus global merge

The same `RC_2026-05` rows were compared in two layouts:

| Layout | Files | Row groups | Bytes | Ordering |
|---|---:|---:|---:|---|
| Independently sorted chunks | 88 | 3,496 | 53,612,379,234 | Each four-million-row source chunk sorted by `author, subreddit, created_utc` |
| Globally merged Parquet | 1 | 3,485 | 46,771,953,794 | Entire month sorted by `author, subreddit, created_utc` |

The chunk dataset was derived one-to-one from the retained chronological
four-million-row chunks. It has the same 349,577,451 rows, 58-column schema,
100,000-row-group setting, and ZSTD codec as the merged file. Producing it by
Parquet-to-Parquet sorting took 658.48 seconds. The chunk dataset is 14.63%
larger because values from the same author are split across files.

Each query used a fresh DuckDB connection with 9 threads and a 25 GB memory
limit. After one warm-up per layout, seven timed repetitions alternated which
layout ran first. The table reports medians. "Full rows" computes a count and
an order-independent hash over the complete row, forcing all columns to be
read without including terminal or network transfer time.

| Query | Matching rows | Merged | Sorted chunks | Chunk/merge ratio |
|---|---:|---:|---:|---:|
| Rare author count (`marvekin`) | 14 | 0.145 s | **0.070 s** | 0.48x |
| Rare author full rows | 14 | 0.421 s | **0.346 s** | 0.82x |
| Active author full rows (`RemyM00n_`) | 2,000 | **0.396 s** | 0.483 s | 1.22x |
| Subreddit count (`AskReddit`) | 3,827,403 | **0.588 s** | 1.294 s | 2.20x |
| Subreddit full rows | 3,827,403 | **36.844 s** | 41.760 s | 1.13x |
| Rare author and subreddit full rows | 6 | 0.434 s | **0.353 s** | 0.81x |
| One-day count (2026-05-15 UTC) | 11,168,982 | 0.780 s | **0.078 s** | 0.10x |
| One-day full rows | 11,168,982 | 40.577 s | **2.556 s** | 0.06x |

All query counts and full-row fingerprints matched between layouts. The rare
author appeared in four chunk files, the active author in 21, the combined
predicate in two, `AskReddit` in all 88, and the selected day in four.

The global merge provides a measurable benefit when qualifying data spans many
chunks: the active-author query was 22% slower on chunks, the subreddit count
was 2.20x slower, and the full-row subreddit scan was 13% slower. The absolute
author latencies remained below half a second in both layouts.

The chunk layout retains the source's chronological file boundaries, even
though rows are author-sorted within each file. File and row-group statistics
therefore prune time ranges effectively. The one-day full-row query was 15.9x
faster on chunks, while the global author sort scattered that day across the
merged file.

For the target workload, the final global merge is not required for fast
single-author retrieval. Independently sorted chunks preserve subsecond author
queries, avoid the expensive month-wide merge, and substantially improve time
filtering. The merged layout is preferable only when the modest improvement
for prolific authors and subreddit-wide scans justifies its merge cost and
12.76% storage reduction relative to the chunk dataset.

## Query-driven Parquet file sizing

The independently sorted layout was also tested at 2M, 4M, 6M, and 8M rows per
file. Each layout contains the same 349,577,451 rows and exact 58-column schema,
uses 100,000-row Parquet groups, and preserves chronological file boundaries.

| Rows/file | Files | Average file | Total bytes |
|---:|---:|---:|---:|
| 2M | 175 | 297 MiB | 54,542,682,750 |
| 4M | 88 | 581 MiB | 53,612,379,234 |
| **6M** | **59** | **858 MiB** | **53,100,958,500** |
| 8M | 44 | 1,141 MiB | 52,663,875,806 |

Five timed repetitions were run with fresh DuckDB connections after one
warm-up per layout. Execution order was rotated so every layout occupied every
position once. The subreddit samples contain 100 rows (`SkatingAdvice`), 9,998
rows (`ultrawidemasterrace`), 100,083 rows (`chile`), and 3,827,403 rows
(`AskReddit`).

### Count-only queries

| Query | Merged | 2M | 4M | 6M | 8M |
|---|---:|---:|---:|---:|---:|
| Rare subreddit | **0.157 s** | 1.115 s | 0.947 s | 0.784 s | 0.643 s |
| Medium subreddit | **0.372 s** | 1.154 s | 1.013 s | 0.904 s | 0.808 s |
| Large subreddit | **0.744 s** | 1.352 s | 1.259 s | 1.133 s | 1.055 s |
| `AskReddit` | **0.803 s** | 1.578 s | 1.454 s | 1.300 s | 1.187 s |
| Rare author, 14 rows | 0.145 s | 0.112 s | 0.066 s | 0.057 s | **0.049 s** |
| One day | 0.649 s | **0.060 s** | 0.066 s | 0.065 s | 0.076 s |

Larger chunk files improve subreddit counts because DuckDB opens fewer files
and reads fewer file footers. Even the 2M layout remains under 1.6 seconds.

### Full-row-forcing queries

These queries hash complete qualifying rows, forcing all columns to be read
without measuring result transfer.

| Query | Merged | 2M | 4M | 6M | 8M |
|---|---:|---:|---:|---:|---:|
| Rare subreddit, 100 rows | **0.821 s** | 1.978 s | 1.816 s | 1.601 s | 1.444 s |
| Medium subreddit, 9,998 rows | **20.094 s** | 31.669 s | 27.646 s | 28.266 s | 30.292 s |
| Large subreddit, 100,083 rows | **31.890 s** | 42.215 s | 41.189 s | 41.226 s | 39.766 s |
| `AskReddit`, 3,827,403 rows | **36.508 s** | 42.730 s | 41.434 s | 41.425 s | 40.369 s |
| Rare author, 14 rows | 0.431 s | 0.405 s | 0.355 s | 0.349 s | **0.333 s** |
| Active author, 2,000 rows | **0.404 s** | 0.664 s | 0.481 s | 0.430 s | 0.410 s |
| Rare author in `AskReddit`, 6 rows | 0.422 s | 0.372 s | 0.332 s | **0.297 s** | 0.307 s |
| One day, 11,168,982 rows | 37.480 s | **2.146 s** | 2.540 s | 2.721 s | 3.340 s |

Subreddit-only full-row queries remain expensive because `subreddit` is the
second sort key and matching rows are spread across authors. File sizing
cannot solve that access pattern; a separate subreddit-oriented derivative
layout is required if tens-of-seconds latency is unacceptable.

### File-size decision

Use **6M rows per file, approximately 858 MiB**, as the balanced default:

- Compared with 4M files, it improved all subreddit counts by 10-17%, rare
  subreddit full-row retrieval by 12%, and active-author retrieval by 11%.
- Medium- and large-subreddit full-row scans were effectively tied with 4M
  files, within 2.3%.
- One-day full-row retrieval was 7% slower than 4M, but remained 13.8x faster
  than the globally merged file.
- It uses 59 files instead of 88 and 15.1 GiB peak RSS during direct creation.

Use **8M rows, approximately 1.1 GiB per file**, when standalone author and
count-only subreddit queries dominate and higher memory use is acceptable. It
had the lowest chunk-layout latency for those queries, but made the
medium-subreddit full-row query 9.6% slower and the one-day full-row query 31%
slower than 4M. The combined author-and-subreddit query was fastest at 6M.

Use **2M rows** only when time-range retrieval is the primary workload. It was
fastest for one-day retrieval but slower for every tested author and subreddit
query, produced 175 files, and used the most storage.
