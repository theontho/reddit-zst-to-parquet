# Conversion Benchmarks

## Geekbench 7 measured machine shootout

The directly measured Geekbench 7 fleet includes macOS, Windows, Omarchy
Linux, and Linux ARM systems. CPU scores use a common Geekbench 7 scale. GPU
scores are shown with their API because Metal, CUDA, OpenCL, and Vulkan
exercise different software stacks and should not be treated as perfectly
interchangeable.

| Machine | Platform | CPU single | CPU multi | Best GPU |
|---|---|---:|---:|---:|
| M5 Max MacBook Pro | macOS | **3,620** | **33,945** | 238,684 Metal |
| Ryzen Mini PC, Windows profile | Windows 11 Pro | **2,055** | 9,635 | **297,964 CUDA** |
| M1 Pro Mac | macOS | 2,024 | **11,715** | 61,431 Metal |
| `lin-big-omarchy` | Omarchy Linux | 2,022 | 8,724 | 194,064 Vulkan |
| `lin-omarchy` | Omarchy Linux | 1,855 | 6,866 | 11,714 Vulkan |
| Ryzen Winbox, Windows profile | Windows 11 Pro | 1,781 | 6,653 | 11,521 Vulkan |
| Celeron ASUS | Linux | 615 | 778 | 1,053 Vulkan |
| UDM SE | Linux ARM Preview | 291 | 668 | Not supported |
| QNAP TS-433 | Linux ARM Preview | 189 | 382 | Not supported |
| Raspberry Pi 4 | Linux ARM Preview | No valid result | No valid result | Not supported |

The M5 Max led CPU performance by a wide margin. It was 78.9% faster
single-core and 189.8% faster multi-core than the measured M1 Pro, while its
Metal GPU score was 3.89x higher. Against `lin-omarchy`, it was 1.95x
single-core, 4.94x multi-core, and 20.38x higher in the cross-API Metal versus
Vulkan GPU comparison.

The selected `lin-big-omarchy` CPU retest scored 2,022 single-core and 8,724
multi-core; its RTX 3090 scored 194,064 Vulkan. Against `lin-omarchy`, the
Ryzen 5 5600X was 9.0% faster single-core and 27.1% faster multi-core. The RTX
3090 was 16.57x the smaller machine's integrated Radeon Vulkan score.

The Windows Ryzen Mini PC and `lin-big-omarchy` are the same physical
Gigabyte B550I system with a Ryzen 5 5600X, 64 GB RAM, and an RTX 3090. The
names distinguish operating-system profiles, not devices. Windows 11 Pro used
the High performance power plan; Omarchy used the performance governor and
performance EPP.

The Windows profile was 1.6% higher single-core, 10.4% higher multi-core, and
8.1% higher in the directly comparable Vulkan GPU test. Its 297,964 CUDA
score remains the highest GPU number in the fleet, but it must not be used to
claim that Windows was 53.5% faster than Linux: CUDA and Vulkan are different
Geekbench backends. The equivalent Vulkan comparison is 209,874 versus
194,064.

The initial `lin-big-omarchy` CPU pass overlapped with other user activity and
scored 1,988 / 8,250. After a 15-second idle wait, with the performance
governor active, 52 GiB memory available, no swap in use, and a 0.78 one-minute
load average, the retest improved single-core by 1.7% and multi-core by 5.7%.
Both runs are retained; the cleaner 2,022 / 8,724 retest is used in the
shootout.

Against the directly measured M1 Pro Mac, `lin-omarchy` was 8.3% lower in
single-core and 41.4% lower in multi-core. The M1 Pro's 61,431 Metal GPU score
was 5.24x the Vega iGPU's 11,714 Vulkan score, but that ratio crosses graphics
APIs.

The Windows Ryzen Winbox and `lin-omarchy` are also two profiles of the same
Lenovo 11JQS1M900, Ryzen 3 PRO 5350GE, 32 GB machine. Omarchy was 4.2% higher
single-core, 3.2% higher multi-core, and 1.7% higher in Vulkan. The older
Windows inventory recorded a 256 GB SATA drive and empty NVMe slot; the
current Omarchy profile uses a 1 TB SK hynix NVMe, so the storage configuration
was upgraded or the old inventory was incomplete.

The Windows Ryzen profile retained the highest measured GPU score with
297,964 CUDA,
although that should not be ranked as a direct like-for-like result against
the M5 Max's 238,684 Metal score. `lin-omarchy` was roughly 3.0x the Celeron
node's single-core score, 8.8x its multi-core score, and 11.1x its Vulkan GPU
score. The ARM appliance build was CPU-only; the Raspberry Pi 4 produced no
valid score because it was OOM-killed during multi-core.

The normalized shootout is tracked in
[`docs/benchmarks/geekbench-7-measured-shootout.json`](benchmarks/geekbench-7-measured-shootout.json).
The `lin-omarchy` node's sanitized console output, environment capture, and
normalized result are retained under
`out/geekbench/lin-omarchy/20260908T064800Z/` and on `lin-omarchy` at the
matching repository-relative path. The equivalent `lin-big-omarchy` record is
under `out/geekbench/lin-big-omarchy/20260908T070141Z/`, with its selected CPU
retest under `out/geekbench/lin-big-omarchy/20260908T071139Z-cpu-retest/`.

## Apple M5 Max 128 GB conversion and merge profile

The imported M5 Max archive contains 595 compact artifacts from the optimized
conversion sweep, complete May RC/RS processing, 25 full merge trials, and
Geekbench 7. The original ZIP is retained under
`out/benchmark-imports/m5-max/20260908T064431Z/` with SHA-256
`52cca333c5e8b0c1e4aa35e1f66a887a6ed8cc63debf65f8b14d0c227b468710`.

The host was an 18-core Apple M5 Max MacBook Pro with 128 GiB unified memory
and a 2 TB internal Apple NVMe. It ran macOS 26.6.2 on AC power in High Power
mode with Microsoft Defender enabled.

### Optimized chunk conversion

| Dataset | Chunk rows | Threads | DuckDB memory | Median total | Throughput |
|---|---:|---:|---:|---:|---:|
| Comments | 18M | 15 | 80 GB | 28.72 s | **626,676 rows/s** |
| Submissions | 5M | 18 | 100 GB | 37.02 s | **135,073 rows/s** |

The RC throughput plateau covered 14M through 18M rows; 16M retained 99% of
peak throughput with a smaller retry unit. RS peaked more clearly at 5M and
slowed at 5.5M and 6M. Compared with the repository's 6M/1.5M, 12-thread,
25 GB profile, the selected RC profile improved throughput by 14.4% and the RS
profile by 50.9%.

The staged threaded decoder was faster than direct DuckDB Zstandard ingestion
on this machine. At the matched 12-thread/25 GB setting, staging reduced RC
time by 45.9% and RS time by 24.2%.

### Complete May processing

| Dataset | Rows | Chunks | Conversion work | Best merge | Conversion + merge | Final size |
|---|---:|---:|---:|---:|---:|---:|
| Comments | 349,577,451 | 20 | 9m 27s | 5m 25s | **14m 52s** | 43.33 GiB |
| Submissions | 47,453,180 | 10 | 6m 08s | 4m 24s | **10m 32s** | 25.73 GiB |

The winning RC merge used 12 threads and 80 GB. The winning RS merge used nine
threads and 64 GB. More resources were not automatically better: the RC
100 GB merge was 30.0% slower than the winner and added 5.76 GiB of swap, while
the RS 100 GB run was 19.4% slower and added 0.69 GiB.

Relative to `lin-omarchy`, the M5 Max's conversion-plus-merge totals were
approximately **5.57x faster for comments** and **5.00x faster for
submissions**. Its chunk-conversion throughput was about 4.03x the matched
`lin-big-omarchy` RC rate and 4.05x its RS rate. These comparisons use
different host-specific resource profiles and describe delivered throughput,
not equal-resource CPU efficiency.

The full normalized profile is tracked in
[`docs/benchmarks/RC_RS_2026-05-m5-max-profile.json`](benchmarks/RC_RS_2026-05-m5-max-profile.json).
Every retained output passed its source-hash, row-count, schema, content, and
physical sort-order validation. Cross-host fingerprint equality was not
asserted because the M5 and Linux full-month harnesses used different schema
projection and fingerprint implementations.

### April-July production conversion

After selecting the M5 profile above, it was used to produce and validate four
consecutive months of comments and submissions. The compact evidence record is
[`RC_RS_2026-04_07-m5-production.json`](benchmarks/RC_RS_2026-04_07-m5-production.json).
It records source and output hashes, row and byte counts, production timings,
resource telemetry, and hashes of the four imported evidence archives.

The production inventory covers **1,589,365,627 rows** and
**311,471,013,097 output bytes** (290.08 GiB):

| Dataset | Rows | Output GiB | Output SHA-256 |
|---|---:|---:|---|
| RC 2026-04 | 338,589,164 | 42.31 | `e837a3e7...f0a003` |
| RC 2026-05 | 349,577,451 | 43.33 | `bd4644c9...900c0e` |
| RC 2026-06 | 347,582,376 | 44.84 | `aa1696e5...06b1801` |
| RC 2026-07 | 371,198,229 | 60.60 | `3299daef...f8868d` |
| RS 2026-04 | 44,966,495 | 24.03 | `fc6f5711...b22b51` |
| RS 2026-05 | 47,453,180 | 25.73 | `d0b277b0...e6de9` |
| RS 2026-06 | 44,257,470 | 24.13 | `cc0e2fa5...cf7083` |
| RS 2026-07 | 45,741,262 | 25.10 | `dac90db5...f7e39` |

The May rows reuse the validated outputs and repeated merge measurements from
the optimization profile above. April, June, and July are single production
runs, so their times are operational measurements rather than repeat medians:

| Dataset | Chunks | Conversion | Merge | Merge rate | Merge strategy |
|---|---:|---:|---:|---:|---|
| RC 2026-04 | 19 | 9m 11s | 6m 51s | 824,732 rows/s | Single global sort |
| RC 2026-06 | 20 | 10m 00s | 7m 07s | 813,560 rows/s | Single global sort |
| RC 2026-07 | 21 | 11m 56s | 20m 04s | 308,295 rows/s | 17 author ranges, then ordered concatenation |
| RS 2026-04 | 9 | 5m 50s | 4m 38s | 161,579 rows/s | Single global sort |
| RS 2026-06 | 9 | 5m 35s | 4m 32s | 162,614 rows/s | Single global sort |
| RS 2026-07 | 10 | 5m 48s | 4m 59s | 153,134 rows/s | Single global sort |

RC July exposed a disk-capacity boundary that was not visible in the smaller
months. A global sort with nine threads and an 80 GB DuckDB limit exhausted its
**339,889,393,539-byte (316.55 GiB)** spill ceiling. The recovery split the
complete author keyspace, including null authors, into 17 disjoint ranges,
sorted each range, and concatenated the already ordered outputs. Range sorting
took 208.16 seconds and concatenation took 995.88 seconds. Validation then
confirmed all 371,198,229 rows, the expected fingerprints and SHA-256, and zero
physical sort violations.

The recovery's measured DuckDB spill peaked at only 9.85 GiB, but that number
excludes the retained 60.60 GiB of range outputs and the growing 60.60 GiB
final output. It must not be interpreted as the complete scratch-space
requirement.

## Full May 2026 Linux chunk-and-merge run

The complete May comment and submission archives were processed on the Ryzen
3 PRO 5350GE (4 cores / 8 threads, 30.7 GiB usable RAM), Linux 7.1.9, and the
internal SK hynix SHGP31 NVMe. Source, chunks, scratch, and final output used
encrypted Btrfs with `compress=zstd:3`. The upstream base was `b44c6d8`, with
the local `benchmarks.full_pipeline` and `benchmarks.merge_tuning` harnesses.
Python was 3.14.7 and DuckDB 1.5.2. No fleet, transfer, or full-file streamed
engine was used.

The production bounded threaded Zstandard reader staged exact source-line
chunks using binary blocks. DuckDB's Python API normalized and sorted each
chunk using the master schema. Missing canonical columns were padded before
merging, so identical chunk schemas did not require `union_by_name=true`.
Both phases used four DuckDB threads and ZSTD Parquet with a 100,000-row-group
request. Chunk memory was 24 decimal GB; the selected merge memory was 16 GB.
All chunks and both tested final-output variants were retained.

### Complete processing results: recommended 16 GB merge profile

| Dataset | Source rows | Chunks | Staging | Chunk conversion | Global merge | Processing total | Final size |
|---|---:|---:|---:|---:|---:|---:|---:|
| Comments | 349,577,451 | 59 x up to 6M | 18.93 min | 29.99 min | **33.79 min** | **82.71 min** | 43.58 GiB |
| Submissions | 47,453,180 | 32 x up to 1.5M | 7.28 min | 22.58 min | **22.73 min** | **52.59 min** | 25.86 GiB |

Processing totals exclude source hashing, output validation, the separate
tuning experiments, and the initial 20 GB merge trials. Adding chunk and final
validation yields 89.22 and 55.22 minutes respectively. These are single full
archive observations, not repeated medians. The original source files were
unchanged.

Neither chunk-conversion workload spilled. Peak chunk RSS was 17.37 GiB for
comments and 19.10 GiB for submissions. The selected final output sizes were
46,794,757,375 and 27,769,717,001 bytes, respectively: 11.68% and 4.60% smaller
than their retained chunk datasets.

### Merge memory: more RAM was not always faster

| Dataset | DuckDB limit | Full merge | Peak process RSS | Peak logical spill | Minimum available RAM | Host swap-out during merge |
|---|---:|---:|---:|---:|---:|---:|
| Comments | 20 GB | 37.44 min | 24.30 GiB | 321.03 GiB | 2.29 GiB | 12.65 GiB |
| Comments | **16 GB** | **33.79 min** | **22.10 GiB** | **314.68 GiB** | **4.41 GiB** | **0** |
| Submissions | 20 GB | 21.87 min | 26.28 GiB | 138.64 GiB | 0.23 GiB | 19.93 GiB |
| Submissions | **16 GB** | **22.73 min** | **20.84 GiB** | **173.21 GiB** | **5.91 GiB** | **0** |

The 16 GB setting made the comment merge 9.7% faster. Submissions were 4.0%
slower but retained substantially more memory headroom and avoided new OS
swap-out. Use 16 GB on this shared 32 GB-class machine. The configured DuckDB
limit is not a process-RSS ceiling. This experiment used a fixed 20-then-16
execution order, so cache and other host activity were not fully controlled.
These observations do not establish an optimal limit for every host.

Spill and RSS were sampled every second. Spill is the sum of logical file sizes,
not allocated NVMe bytes; Btrfs compression changes physical usage. Swap counters
are host-wide and include other processes. Existing inactive swap remained in
use during the 16 GB runs; zero swap-out does not mean swap was disabled.

### Merge thread calibration

The first four retained chunks were merged with one and four threads, using
20 GB memory and identical output settings. Two runs per configuration used
order 1,4,4,1, with no cache flushing and no concurrent conversion.

| Dataset sample | Rows | One-thread median | Four-thread median | Speedup |
|---|---:|---:|---:|---:|
| Comments | 24,000,000 | 234.15 s | 120.21 s | **1.95x** |
| Submissions | 6,000,000 | 225.33 s | 136.20 s | **1.65x** |

These are subset speedups, not measured full-month single-thread speedups.
The complete dataset was merged with four threads. DuckDB's native parallel
streaming k-way sort was retained rather than adding Python per-row merging or
pairwise rewrite passes; see the [full benchmark rationale](../benchmarks/README.md).

### Correctness and artifacts

Each source SHA-256 matched the canonical handoff. Every chunk matched its exact
staged source row count. Both full merge variants matched the retained chunks'
canonical schemas, per-column non-null counts, and two order-independent
full-row fingerprints (XOR and sum). Physical global ordering was checked in
bounded Arrow batches. Full-row fingerprints also matched between the 16 GB and
20 GB outputs; file byte sizes can differ because sort ties and encoding can
produce different physical layouts.

The benchmark host retains the raw run under
`out/full-pipeline/20260908-full-may-linux/`: `summary.json` consolidates the
run, the base dataset directories retain all chunks and the 20 GB output, and
the `RC_2026-05-merge16` / `RS_2026-05-merge16` directories contain the
recommended final outputs. Per-dataset `result.json`, `phases.jsonl`,
`telemetry.jsonl`, DuckDB query profiles, environment captures, and logs
preserve detailed measurements. Large generated artifacts remain ignored by
Git; the normalized machine profile is tracked in
[`docs/benchmarks/RC_RS_2026-05-omarchy-full-pipeline.json`](benchmarks/RC_RS_2026-05-omarchy-full-pipeline.json).

## Matched `lin-big-omarchy` chunking comparison

The complete May archives were processed again on `lin-big-omarchy`, an
Omarchy 4.0.2 host with a Ryzen 5 5600X, 6 physical cores, 64 GiB RAM, and a
Sabrent NVMe using encrypted Btrfs with `compress=zstd:3`. The comparison
deliberately retained the smaller host's four-thread and 24 GB limits, the same
chunk sizes, Python 3.14.7, DuckDB 1.5.2, Python Zstandard 0.25.0, sort keys,
row-group request, and compression settings.

| Dataset | Host | Staging | Chunk conversion | Validation | Chunking total | Throughput |
|---|---|---:|---:|---:|---:|---:|
| Comments | `lin-big-omarchy` | 15.25 min | 22.85 min | 2.43 min | **38.10 min** | **152,923 rows/s** |
| Comments | `lin-omarchy` | 18.93 min | 29.99 min | 3.31 min | 48.92 min | 119,098 rows/s |
| Submissions | `lin-big-omarchy` | 5.80 min | 19.00 min | 1.02 min | **24.79 min** | **31,901 rows/s** |
| Submissions | `lin-omarchy` | 7.28 min | 22.58 min | 1.33 min | 29.86 min | 26,487 rows/s |

`lin-big-omarchy` was **1.284x faster for comments** and **1.204x faster for
submissions**, reducing chunking wall time by 22.12% and 16.97%. Peak RSS was
17.02 GiB for comments and 18.57 GiB for submissions, and neither workload
spilled. Average process CPU use including validation was 2.75 and 2.29 cores,
so these measurements do not establish how either host scales beyond four
configured DuckDB threads.

Both machines processed identical source hashes and row counts. Every one of
the 59 RC and 32 RS output chunks matched its reference content. Output byte
sizes differed by only 120,183 bytes for RC and 68,597 bytes for RS; Parquet
encoding and equal-key row ordering can produce byte-level differences without
changing content.

This comparison covers source staging, per-chunk normalization and sorting,
Parquet creation, and validation. It does not include a global merge and must
not be used to predict merge scaling. The raw local profiles and telemetry are
under `out/benchmark-2026-05-lin-big-omarchy/results-clean/`; the normalized
result is tracked in
[`docs/benchmarks/RC_RS_2026-05-lin-big-omarchy-chunking.json`](benchmarks/RC_RS_2026-05-lin-big-omarchy-chunking.json).

### `lin-big-omarchy` four-versus-six-thread follow-up

A matched 6M-comment test compared four and six threads at the same 24 GB
memory limit, using three repetitions per profile:

| Threads | Median stage | Median Parquet | Median total | Throughput |
|---:|---:|---:|---:|---:|
| 4 | 15.32 s | 20.72 s | 36.11 s | 166,146 rows/s |
| **6** | **15.11 s** | **19.02 s** | **34.18 s** | **175,532 rows/s** |

Six threads increased median throughput by **5.65%** and reduced elapsed time
by **5.35%**. Staging improved only 1.4%, within likely run noise; the
DuckDB/Parquet phase improved 8.22%. All six outputs contained 6,000,000 rows,
shared the same content fingerprint, and had zero physical sort-order
violations. Six threads are better than four for this workload, but a wider
sweep is still required to determine whether six is optimal.

The normalized comparison is tracked in
[`docs/benchmarks/RC_2026-05-lin-big-omarchy-thread-sweep.json`](benchmarks/RC_2026-05-lin-big-omarchy-thread-sweep.json).

This document records conversion measurements used to choose the chunking and
sorting strategy. Generated Parquet files and full logs are intentionally not
tracked; the compact raw results are in
[`docs/benchmarks/RC_2026-05-chunk-sizing.json`](benchmarks/RC_2026-05-chunk-sizing.json)
and
[`docs/benchmarks/RS_2026-05-chunk-sizing.json`](benchmarks/RS_2026-05-chunk-sizing.json).
The cross-platform Mac and Windows optimization results are in
[`docs/benchmarks/RC_2026-05-windows-optimization.json`](benchmarks/RC_2026-05-windows-optimization.json).
The same benchmark rerun on the former Windows machine after installing Linux
is in
[`docs/benchmarks/RC_2026-05-linux-comparison.json`](benchmarks/RC_2026-05-linux-comparison.json).
The native-NVMe 4M/6M follow-up is in
[`docs/benchmarks/RC_2026-05-linux-nvme-chunks.json`](benchmarks/RC_2026-05-linux-nvme-chunks.json).
The post-reinstall Omarchy RC/RS threaded-decoder suite is in
[`docs/benchmarks/RC_RS_2026-05-omarchy-suite.json`](benchmarks/RC_RS_2026-05-omarchy-suite.json).
The complete Omarchy chunk-and-merge machine profile is in
[`docs/benchmarks/RC_RS_2026-05-omarchy-full-pipeline.json`](benchmarks/RC_RS_2026-05-omarchy-full-pipeline.json).
The matched larger-host chunking comparison is in
[`docs/benchmarks/RC_RS_2026-05-lin-big-omarchy-chunking.json`](benchmarks/RC_RS_2026-05-lin-big-omarchy-chunking.json).
The larger host's four-versus-six-thread follow-up is in
[`docs/benchmarks/RC_2026-05-lin-big-omarchy-thread-sweep.json`](benchmarks/RC_2026-05-lin-big-omarchy-thread-sweep.json).
Use the tracked [`benchmarks.conversion`](../benchmarks/README.md) harness to
rerun the staged or direct conversion paths on macOS, Windows, or Linux. It
captures environment metadata and validates source hashes, row counts,
schemas, full-row fingerprints, and physical sort order.

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

### Cross-platform block-streaming optimization

A matched legacy run on the Ryzen 3 PRO 5350GE Windows node took 12,873 seconds
to produce the same 88 four-million-row chunks that the M1 Pro produced in
3,699 seconds. Windows chunking was 3.48x slower even though the independent
hardware suite measured much smaller gaps:

| Independent benchmark | Mac/Windows speed ratio |
|---|---:|
| Geekbench 7 single-core | 1.14x |
| Geekbench 7 multi-core | 1.76x |
| fio sequential read | 1.82x |
| fio sequential write | 1.91x |
| 7-Zip compression | 1.54x |

The legacy converter decoded every row into a Python string, retained four
million strings at once, and wrote them through a Windows text stream before
DuckDB read them again. A later line-streaming implementation removed the
list, but still performed four million Python read/decode/encode/write cycles.
On Windows, staging the first 4M rows that way took 33.10 seconds.

The optimized implementation keeps the Zstandard subprocess in binary mode and
copies 8 MiB blocks. It counts newlines in C-backed byte operations and retains
only the bytes after a chunk boundary. The same staging operation fell to
12.24 seconds, a **2.70x improvement**, while preserving exact source bytes.

The complete staged, normalized, author-sorted 4M conversion used four DuckDB
threads on Windows and nine on the Mac:

| Machine/path | Repetitions | Median stage | Median Parquet | Median total | Throughput |
|---|---:|---:|---:|---:|---:|
| M1 Pro, binary blocks | 3 | 7.41 s | 8.10 s | 15.51 s | 257,832 rows/s |
| Ryzen 5350GE, text lines | 1 | 33.10 s | 22.43 s | 55.53 s | 72,034 rows/s |
| **Ryzen 5350GE, binary blocks** | **3** | **12.24 s** | **22.27 s** | **34.48 s** | **115,997 rows/s** |

The optimized protection-enabled comparison is:

| Phase/path | M1 Pro | Ryzen 5350GE | Windows/Mac time |
|---|---:|---:|---:|
| Binary staging | 7.41 s | 12.24 s | 1.65x |
| DuckDB to sorted Parquet | 8.10 s | 22.27 s | 2.75x |
| **Staged total** | **15.51 s** | **34.48 s** | **2.22x** |
| Direct DuckDB ZSTD to sorted Parquet | 23.42 s | 44.40 s | 1.90x |

The optimized staged path takes 55.0% less wall time on the Mac. The direct
result is a one-pass comparison, not the production chunking design: applying
`LIMIT/OFFSET` repeatedly to direct ingestion would decompress the source again
for every chunk.

Disabling Defender real-time, behavior, IOAV, and script scanning did not
improve this path. Three otherwise identical Windows runs had a median of
35.79 seconds and 111,768 rows/s, 3.78% slower than the 34.48-second
protection-enabled median and within ordinary run variance. This was not a
fully disabled Defender test: the antivirus engine, protected service, process,
and file-system filter remained loaded while real-time and on-access scanning
were verified inactive. All runs produced the same 4M-row content fingerprint.

Binary block streaming makes the optimized Windows path 2.22x slower than the
optimized Mac path instead of 3.48x slower than the historical Mac baseline.
The staging ratio is 1.65x, within the independent storage/compression range.
The remaining 2.75x Parquet-phase gap is dominated by JSON parsing, sorting,
memory traffic, and compression on a four-core DDR4 system rather than NVMe
throughput.

Four DuckDB threads were marginally best on the four-core/eight-thread Ryzen:
34.48 seconds versus 37.51 seconds with seven threads and 34.88 seconds with
eight. The engine therefore caps chunk workers at physical cores as well as
the memory-derived limit. High performance versus Balanced power plans made no
measurable difference (34.58 versus 34.48 seconds), so Balanced was restored.

The same Ryzen machine was wiped and retested under Ubuntu Linux using the
tracked benchmark harness. The source remained on the same SanDisk SATA SSD,
mounted read-only through Linux's NTFS/FUSE path, while staging, Parquet output,
and DuckDB temporary data used the same SK hynix NVMe. Linux used the default
`amd-pstate-epp` `powersave` governor with its energy-performance preference
set to `performance`; no antivirus was installed. The source size and SHA-256
matched the Mac and Windows runs exactly.

| Machine/OS | Median stage | Median Parquet | Median total | Throughput |
|---|---:|---:|---:|---:|
| M1 Pro, macOS | 7.41 s | 8.10 s | 15.51 s | 257,832 rows/s |
| Ryzen 5350GE, Windows | 12.24 s | 22.27 s | 34.48 s | 115,997 rows/s |
| **Ryzen 5350GE, Linux** | **14.46 s** | **17.41 s** | **31.96 s** | **125,165 rows/s** |

Linux was **7.3% faster overall than Windows** on the identical Ryzen hardware.
Its DuckDB parse, normalize, sort, and Parquet phase was 21.8% faster, while
staging was 18.2% slower. A native-NVMe follow-up below showed that NTFS/FUSE
was not the cause of the staging gap. The M1 Pro remained 2.06x faster than
this initial Ryzen/Linux run overall, with 1.95x and 2.15x advantages in the
staging and Parquet phases respectively.

All three Linux runs produced 4,000,000 rows with content fingerprint
`7365298922509737757`, the same schema as the Mac and Windows outputs, and zero
physical sort-order violations. The three totals were 32.26, 31.64, and 31.96
seconds.

#### Native-NVMe Linux follow-up

The complete 47.8 GB source was copied to the SK hynix NVMe's native ext4
filesystem and its SHA-256 was reverified before rerunning. Source, staged
JSONL, DuckDB temporary data, and Parquet output were therefore all on the
same NVMe:

| Rows | Median stage | Median Parquet | Median total | Throughput | Output |
|---:|---:|---:|---:|---:|---:|
| 4M | 14.43 s | 17.19 s | 31.52 s | 126,907 rows/s | 574 MiB |
| 6M | 21.98 s | 24.94 s | 46.92 s | 127,888 rows/s | 858 MiB |

Moving the source from SATA NTFS/FUSE to the internal NVMe improved the 4M
median by only 1.4% overall and improved staging by just 0.2%. Zstandard
decompression, rather than source-drive throughput, was the limiting part of
staging. Against Windows on the same Ryzen hardware, the native-NVMe Linux run
was 8.6% faster overall: Linux staging remained 17.9% slower, while its
DuckDB/Parquet phase was 22.8% faster. The M1 Pro was 2.03x faster overall.

The 6M chunk was 0.8% faster per row than 4M, which is within normal run
variance and confirms that chunk size does not materially change throughput.
All six native-NVMe runs matched their expected row count and schema, had
consistent fingerprints within each row count, and had zero physical
sort-order violations. The 4M fingerprint was `7365298922509737757`; the 6M
fingerprint was `2755064075187337237`.

#### Omarchy threaded-decoder suite

After reinstalling the same Ryzen system with Omarchy, the complete May
comment and submission sources were copied to the internal SK hynix NVMe and
reverified. The tracked version-2 harness at commit `b44c6d8` used the bounded
`python-threaded` decoder, four physical-core threads, a 25 GB DuckDB limit,
and three repetitions. The Btrfs filesystem used transparent Zstandard
compression, and the CPU governor and energy preference both reported
`performance`.

| Dataset | Rows | Median stage | Median Parquet | Median total | Throughput |
|---|---:|---:|---:|---:|---:|
| Comments | 4M | 13.03 s | 21.58 s | 34.61 s | 115,578 rows/s |
| Comments | 6M | 20.35 s | 32.92 s | 54.52 s | 110,055 rows/s |
| Submissions | 500k | 4.61 s | 21.40 s | 26.28 s | 19,023 rows/s |
| Submissions | 1.5M | 13.19 s | 38.75 s | 51.94 s | 28,878 rows/s |

All twelve outputs matched the expected source hash, row count, schema,
content fingerprint within each workload, and physical sort order. The
results validate both RC and wider RS data through the new decoder, including
concatenated-frame and error-propagation behavior covered by the regression
suite.

This is not a strict performance comparison with the prior Ubuntu/ext4 run:
the distribution, kernel, Python version, filesystem, mount compression, and
power governor all changed. The Omarchy staging medians were 9.7% lower at 4M
and 7.4% lower at 6M, but slower Parquet phases made total throughput lower.
Use the suite as a current reproducibility baseline, not as evidence that the
OS reinstall itself improved or regressed DuckDB.

The 4M Windows result was slightly faster per row than 2M and 6M:

| Rows | Total | Throughput |
|---:|---:|---:|
| 2M | 17.47 s | 114,452 rows/s |
| **4M** | **34.48 s** | **115,997 rows/s** |
| 6M | 53.93 s | 111,260 rows/s |

These differences are small enough that memory and query-layout requirements
remain the primary chunk-size criteria. The key cross-platform optimization is
binary block staging, not a platform-specific chunk size, power plan, or
weakened antivirus protection.

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
