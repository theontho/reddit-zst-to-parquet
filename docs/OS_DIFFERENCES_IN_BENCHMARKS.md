# Operating-system differences in the measured benchmarks

This document explains what the repository's September 2026 benchmarks say
about macOS, Windows, Ubuntu, and Omarchy Linux. It focuses on operating-system
effects, but also identifies where hardware, drivers, filesystems, runtime
versions, power settings, or converter changes prevent a clean OS-only
conclusion.

## Short version

- On the same Ryzen 3 PRO 5350GE machine and matched 4-million-comment
  conversion, Ubuntu completed the workload **7.3% faster than Windows** when
  reading the same source from the same SATA SSD. With the source copied to
  native NVMe, Ubuntu was **8.6% faster overall**.
- The advantage was phase-specific. Ubuntu staging was about **18% slower**,
  while its DuckDB parse, normalize, sort, compress, and Parquet phase was
  about **22% faster**. "Linux was faster" hides two effects moving in opposite
  directions.
- A later Omarchy run on that physical machine was effectively tied with
  Windows overall: **34.61 seconds on Omarchy versus 34.48 seconds on
  Windows**. It is not a controlled OS comparison because the decoder, kernel,
  Python, filesystem, filesystem compression, and power policy had changed.
- Same-hardware Geekbench 7 differences were generally smaller than
  cross-machine differences. Omarchy was 3-4% ahead of Windows on the 5350GE
  system, while Windows was 2% ahead single-core and 10% ahead multi-core on
  the Ryzen 5 5600X system.
- Converter design mattered much more than an OS tweak. Replacing Windows
  text-line staging with 8 MiB binary block streaming reduced staging from
  **33.10 to 12.24 seconds** and total time from **55.53 to 34.48 seconds**.
- Disabling several Microsoft Defender real-time protections did not improve
  the Windows result. The median became 3.78% slower, within normal run
  variance.
- Geekbench CPU scores are useful context, not a conversion-throughput model.
  GPU scores must retain their API label: CUDA, Metal, OpenCL, and Vulkan
  results are not directly interchangeable.

## How to read the comparisons

The measurements fall into three evidence classes:

| Class | Meaning | Examples |
|---|---|---|
| Matched or near-controlled | Same physical hardware, source bytes, row count, output requirements, DuckDB version, thread count, and memory limit | Ryzen 5350GE Windows versus Ubuntu conversion |
| Same hardware, changed software stack | Same physical machine, but important runtime, filesystem, decoder, or configuration changes remain | Ryzen 5350GE Ubuntu versus Omarchy conversion; Windows versus Omarchy Geekbench |
| Cross-hardware capacity | Different CPUs, memory capacities, storage, and host-specific tuning | M1 Pro, M5 Max, 5350GE, and 5600X conversion rates |

Even the best reinstall comparison is not a laboratory OS kernel test. Windows
and Ubuntu necessarily used different kernels, system libraries, process and
filesystem implementations, and protection stacks. The benchmark does,
however, represent the practical result of running the same validated
conversion workload on the same physical computer under each installed OS.

## Physical machines with multiple OS profiles

Two x86 systems were measured under both Windows and Omarchy. The smaller
Ryzen system was also measured under Ubuntu between those installations.

| Physical system | Shared hardware | Measured profiles |
|---|---|---|
| Lenovo `11JQS1M900` | Ryzen 3 PRO 5350GE, 4 cores/8 threads, about 32 GB RAM, integrated Radeon graphics | Windows 11 Pro, Ubuntu Linux, Omarchy 4.0.2 |
| Gigabyte B550I system | Ryzen 5 5600X, 6 cores/12 threads, 64 GB RAM, RTX 3090 | Windows 11 Pro, Omarchy 4.0.2 |

The Lenovo's recorded storage inventory changed. The older Windows inventory
reported a 256 GB SATA device and an empty NVMe slot, while later Linux runs
used a 1 TB SK hynix NVMe. The matched Windows/Ubuntu conversion records are
more precise: source placement and output placement are recorded separately,
and the Ubuntu native-NVMe follow-up directly tested whether source storage
explained the result.

## Geekbench versions and complete measured inventory

Two Geekbench generations were used. Scores from different major versions
must not be put in the same ranking or used to calculate performance changes.

| Benchmark generation | Exact version | Machines covered | Use in this document |
|---|---|---|---|
| Current shootout | **Geekbench 7.0.0** | M5 Max, M1 Pro, Windows and Omarchy Ryzen systems, Celeron ASUS, UDM SE, QNAP, and attempted Raspberry Pi 4 | Primary CPU/GPU comparison |
| Earlier fleet run | **Geekbench 6.7.1** | M1 Pro, Windows 5350GE, Celeron ASUS, Raspberry Pi 4 | Historical reference only |
| Earlier Mini PC run | **Geekbench 6.4.0** | Windows 5600X/RTX 3090 system | Historical reference only; minor-version mismatch with 6.7.1 |

### Complete Geekbench 7.0.0 shootout

All rows below are directly measured. CPU scores are comparable within
Geekbench 7. GPU scores retain their compute API because CUDA, Metal, OpenCL,
and Vulkan exercise different software stacks.

| Machine and OS profile | CPU single | CPU multi | Measured GPU compute |
|---|---:|---:|---|
| M5 Max MacBook Pro, macOS 26.6.2 | **3,620** | **33,945** | 238,684 Metal |
| Ryzen 5 5600X/RTX 3090, Windows 11 Pro | 2,055 | 9,635 | 297,964 CUDA; 187,778 OpenCL; 209,874 Vulkan |
| M1 Pro MacBook Pro, macOS | 2,024 | **11,715** | 61,431 Metal; 35,576 OpenCL |
| Ryzen 5 5600X/RTX 3090, Omarchy | 2,022 | 8,724 | 194,064 Vulkan |
| Ryzen 3 PRO 5350GE, Omarchy | 1,855 | 6,866 | 11,714 Vulkan |
| Ryzen 3 PRO 5350GE, Windows 11 Pro | 1,781 | 6,653 | 10,177 OpenCL; 11,521 Vulkan |
| Celeron N4500 ASUS, Linux | 615 | 778 | 1,053 Vulkan |
| UniFi Dream Machine SE, Debian-based appliance OS | 291 | 668 | Not supported |
| QNAP TS-433, QNAP Linux appliance | 189 | 382 | Not supported |
| Raspberry Pi 4, Linux | No valid result | No valid result | Not supported |

The Raspberry Pi 4 reached the multi-core phase but was OOM-killed, so the
Geekbench 7 attempt did not produce a valid score. The UDM SE and QNAP used
the Linux ARM build and were CPU-only.

### Where is the Ubuntu Geekbench result?

There is **no retained Geekbench run from the Lenovo 5350GE's temporary Ubuntu
installation**. Ubuntu was measured extensively with the validated Reddit
conversion harness, including same-SATA-source and native-NVMe runs, but the
machine was reinstalled with Omarchy before a Geekbench result was recorded.

The score of 1,855 single-core, 6,866 multi-core, and 11,714 Vulkan belongs to
the later **Omarchy** installation. It must not be relabeled as Ubuntu. The
Windows score on the same physical machine was measured, so the available
Geekbench OS pair is Windows versus Omarchy; the available conversion OS
comparison includes Windows, Ubuntu, and Omarchy.

This missing Ubuntu data limits what can be concluded:

- Geekbench cannot quantify Ubuntu versus Omarchy on this machine.
- The Ubuntu conversion advantage cannot be attributed to a corresponding
  Geekbench CPU-score advantage.
- A future controlled rerun would need the same Geekbench 7 version, firmware,
  power policy, idle conditions, memory/storage configuration, and preferably
  multiple runs under each OS.

### Historical Geekbench 6 results

These results explain older numbers that may appear in benchmark notes or
session history. Geekbench 6 scores are not directly comparable with the
Geekbench 7 table.

| Machine and OS profile | Version | CPU single | CPU multi | GPU compute |
|---|---|---:|---:|---|
| M1 Pro MacBook Pro, macOS | 6.7.1 | **2,252** | **11,431** | Not retained in this fleet table |
| Ryzen 5 5600X/RTX 3090, Windows 11 Pro | 6.4.0 | 1,958 | 7,695 | 216,141 OpenCL; 217,555 Vulkan |
| Ryzen 3 PRO 5350GE, Windows | 6.7.1 | 1,444 | 5,709 | Not retained in this fleet table |
| Celeron N4500 ASUS, Linux | 6.7.1 | 518 | 661 | Not retained in this fleet table |
| Raspberry Pi 4, Linux | 6.7.1 | 257 | 651 | Not supported |

The Windows 5600X result used Geekbench 6.4.0 while the other historical CPU
runs used 6.7.1. That result is useful as an approximate GB6 reference, but
the minor-version mismatch should remain visible. The later Geekbench 7.0.0
shootout was created to put the directly measured fleet on one current major
version.

## Same-hardware Geekbench 7 results

### Ryzen 3 PRO 5350GE: Windows versus Omarchy

| Profile | Single-core | Multi-core | Vulkan GPU |
|---|---:|---:|---:|
| Windows 11 Pro | 1,781 | 6,653 | 11,521 |
| Omarchy Linux | **1,855** | **6,866** | **11,714** |
| Omarchy difference | **+4.2%** | **+3.2%** | **+1.7%** |

This pair shows near-parity with a small Omarchy lead. It does not prove that
Omarchy itself adds 3-4%: the installations, storage configuration, firmware
state, drivers, background activity, and benchmark date were not held
constant. It does show that this machine did not suffer a large CPU or Vulkan
regression after moving from Windows to Omarchy.

### Ryzen 5 5600X and RTX 3090: Windows versus Omarchy

| Profile | Single-core | Multi-core | Vulkan GPU |
|---|---:|---:|---:|
| Windows 11 Pro, High performance | **2,055** | **9,635** | **209,874** |
| Omarchy Linux, performance governor/EPP | 2,022 | 8,724 | 194,064 |
| Omarchy difference | **-1.6%** | **-9.5%** | **-7.5%** |

Windows led this pair, especially in multi-core CPU and Vulkan GPU. The
Omarchy CPU number is the cleaner idle retest. An earlier run performed while
the machine was busy scored 1,988 single-core and 8,250 multi-core; waiting
for the host to become idle raised the selected score by 1.7% and 5.7%.
Background activity can therefore be comparable to, or larger than, some
claimed OS differences.

The RTX 3090 also scored 297,964 with CUDA on Windows, but that number must not
be compared directly with the 194,064 Linux Vulkan result. The like-for-like
comparison is Windows Vulkan 209,874 versus Omarchy Vulkan 194,064.

### What the Geekbench pairs support

The two dual-OS machines point in different directions:

- Omarchy led Windows by 3-4% on the 5350GE CPU.
- Windows led Omarchy by 2-10% on the 5600X CPU.
- Vulkan differed by 2% on the integrated Radeon and 8% on the RTX 3090.

That is evidence against applying a universal "Linux is X% faster" or
"Windows is X% faster" adjustment. CPU topology, firmware, scheduler behavior,
drivers, power policy, memory behavior, and run cleanliness all matter.

## Matched Reddit conversion: Windows versus Ubuntu

The strongest practical OS comparison used the Lenovo Ryzen 3 PRO 5350GE
machine and the first 4,000,000 comments from `RC_2026-05.zst`.

The source archive had SHA-256
`b780399265b98536a4dd29cb1605c8b607fd4297200b900047e2aace6ea6e4cc`.
The selected runs used:

- four DuckDB threads;
- a 25 GB DuckDB memory limit;
- 8 MiB binary block staging;
- DuckDB 1.5.2;
- ZSTD-compressed Parquet;
- sorting by `author, subreddit, created_utc`; and
- three repetitions with row-count, schema, content-fingerprint, and physical
  sort-order validation.

| OS/source profile | Stage | DuckDB/Parquet | Total | Throughput |
|---|---:|---:|---:|---:|
| Windows, source on SATA | **12.24 s** | 22.27 s | 34.48 s | 115,997 rows/s |
| Ubuntu, same SATA source through NTFS/FUSE | 14.46 s | 17.41 s | 31.96 s | 125,165 rows/s |
| Ubuntu, source copied to native ext4 NVMe | 14.43 s | **17.19 s** | **31.52 s** | **126,907 rows/s** |

### Same source placement: Windows versus Ubuntu

With both systems reading the source from the same SATA SSD:

- Ubuntu staging was **18.2% slower**.
- Ubuntu's DuckDB/Parquet phase was **21.8% faster**.
- Ubuntu used **7.3% less total wall time** and delivered about **7.9% more
  rows per second**.

The Linux system used Ubuntu's default `amd-pstate-epp` `powersave` governor
with energy-performance preference set to `performance`. Windows used the
Balanced power plan for the selected runs. No antivirus was installed on
Ubuntu; Microsoft Defender was enabled for the selected Windows baseline.

### Native-NVMe Ubuntu follow-up

Copying and hash-verifying the complete 47.8 GB source onto the Linux NVMe
changed staging from 14.46 to 14.43 seconds, only **0.2%**. Total time improved
by **1.4%**. This rules out the SATA NTFS/FUSE source path as the meaningful
cause of Linux's slower staging.

Against Windows, the native-NVMe Ubuntu result was:

- **17.9% slower** in staging;
- **22.8% faster** in DuckDB/Parquet; and
- **8.6% faster** overall.

The staging phase was limited mainly by Zstandard decompression and associated
data handling, not by sequential source-drive bandwidth. The total Linux
advantage came from the DuckDB-heavy phase.

## The later Omarchy result on the same 5350GE

After another reinstall, the machine ran Omarchy 4.0.2 with Linux 7.1.9,
Python 3.14.7, `python-zstandard`/libzstd 1.5.7, encrypted Btrfs with
`zstd:3` transparent compression, and performance governor/EPP settings. The
committed bounded threaded Python decoder was used instead of the earlier
Ubuntu benchmark's decoder stack.

| Profile | Stage | DuckDB/Parquet | Total | Throughput |
|---|---:|---:|---:|---:|
| Ubuntu, native ext4 NVMe | 14.43 s | **17.19 s** | **31.52 s** | **126,907 rows/s** |
| Omarchy, compressed Btrfs NVMe | **13.03 s** | 21.58 s | 34.61 s | 115,578 rows/s |
| Windows baseline | 12.24 s | 22.27 s | 34.48 s | 115,997 rows/s |

Omarchy staged 9.7% faster than the Ubuntu native-NVMe median, but its
DuckDB/Parquet phase was about 25.5% slower and its total was about 9.8%
slower. Omarchy and Windows were within 0.4% overall.

Those differences must not be assigned to the distribution alone. Between
Ubuntu and Omarchy, all of the following changed:

- distribution and kernel;
- Python and Zstandard integration;
- decoder implementation;
- ext4 versus compressed Btrfs;
- power governor;
- installed system state and background services; and
- benchmark harness revision.

The Omarchy numbers are a reproducible current-machine baseline, not a clean
Ubuntu-versus-Omarchy experiment.

## Why the original Windows gap was misleading

The historical full-month Windows chunking run took 12,873 seconds, versus
3,699 seconds on the M1 Pro Mac: a **3.48x** time ratio. Independent hardware
benchmarks predicted much smaller Mac advantages:

| Independent benchmark | M1 Pro/Windows Ryzen ratio |
|---|---:|
| Geekbench 7 single-core | 1.14x |
| Geekbench 7 multi-core | 1.76x |
| Sequential read | 1.82x |
| Sequential write | 1.91x |
| 7-Zip compression | 1.54x |

Most of the surprising historical gap came from the old converter. It decoded
each row into a Python string, retained millions of strings, then wrote them
through a Windows text stream before DuckDB read the data again.

| Windows implementation | Stage | DuckDB/Parquet | Total | Throughput |
|---|---:|---:|---:|---:|
| Text-line staging | 33.10 s | 22.43 s | 55.53 s | 72,034 rows/s |
| Binary block staging | **12.24 s** | **22.27 s** | **34.48 s** | **115,997 rows/s** |

Binary block staging made the staging phase **2.70x faster** and improved
whole-chunk throughput by about **61%**. After that fix, the optimized Windows
path was 2.22x slower than the M1 Pro rather than the historical 3.48x. The
remaining gap was concentrated in JSON parsing, normalization, sorting, memory
traffic, compression, and Parquet writing on a four-core DDR4 system.

The lesson is that an application path can interact badly with one platform
without the OS being intrinsically that much slower. Optimize and validate the
shared algorithm before interpreting a large platform ratio.

## Factors tested that did not explain the result

### Microsoft Defender

The selected Windows baseline kept Defender enabled. A separate three-run test
disabled real-time monitoring, behavior monitoring, IOAV protection, and
script scanning while the antivirus engine, service, process, and filesystem
filter remained loaded.

| Windows protection profile | Median total | Throughput |
|---|---:|---:|
| Selected protection-enabled baseline | **34.48 s** | **115,997 rows/s** |
| Several real-time protections disabled | 35.79 s | 111,768 rows/s |

The reduced-protection test was 3.78% slower, within ordinary variance.
Weakening these protections did not optimize this workload.

### Windows power plan

Balanced and High performance produced effectively the same selected workload
time: 34.48 versus 34.58 seconds. Balanced was restored. Power profiles still
need to be recorded because they can matter on other hardware, but they did
not explain this result.

### Source filesystem and drive

Moving the source from SATA NTFS/FUSE to native ext4 NVMe improved Ubuntu
staging by only 0.2%. Source-drive throughput was not the bottleneck for this
sequential compressed input.

### Chunk size

On Ubuntu native NVMe, 4M and 6M chunks sustained 126,907 and 127,888 rows/s,
a difference of 0.8% within normal variance. Chunk size should be chosen for
memory headroom, retry size, and output-file count rather than an assumed OS
throughput boost.

### Excess thread count

On the four-core Windows Ryzen, four DuckDB threads completed the workload in
34.48 seconds. Seven threads took 37.51 seconds and eight took 34.88 seconds.
Logical processors did not guarantee a gain; physical-core count was the safer
cap.

### Benchmark host activity

The busy-to-idle 5600X Geekbench retest improved multi-core by 5.7%. Small OS
differences should not be interpreted without controlling foreground and
background activity, thermal state, power, swap, and available memory.

## Cross-hardware conversion context

These results describe delivered capacity, not OS effects, because the
machines and host-specific resource profiles differ.

| Host/profile | Representative measured workload | Throughput |
|---|---|---:|
| M5 Max, macOS, 15 threads/80 GB | 18M-comment optimized chunk | **626,676 rows/s** |
| M1 Pro, macOS, 9 threads/25 GB | Matched 4M-comment chunk | **257,832 rows/s** |
| Ryzen 5 5600X, Omarchy, 6 threads/24 GB | 6M-comment chunk | **175,532 rows/s** |
| Ryzen 3 PRO 5350GE, Ubuntu, 4 threads/25 GB | Matched 4M-comment chunk | **126,907 rows/s** |
| Ryzen 3 PRO 5350GE, Windows, 4 threads/25 GB | Matched 4M-comment chunk | **115,997 rows/s** |
| Ryzen 3 PRO 5350GE, Omarchy, 4 threads/25 GB | 4M-comment current-stack chunk | **115,578 rows/s** |

The M5 Max profile used much more CPU and memory and a larger, independently
tuned chunk. Its result is useful for capacity planning but cannot quantify a
macOS advantage. Likewise, the M1 Pro versus Ryzen difference combines
architecture, core count, memory subsystem, storage, and OS.

Full-month measurements reinforce the same distinction. The M5 Max completed
comment conversion plus merge in 14m 52s and submissions in 10m 32s. It was
approximately 5.57x and 5.00x faster than the smaller 5350GE Omarchy machine,
but those are complete-system throughput ratios, not OS multipliers.

## Geekbench versus real conversion throughput

Geekbench helps characterize CPU capacity, but this pipeline also depends on:

- Zstandard decompression;
- Python or native block movement;
- JSON parsing and type normalization;
- memory bandwidth and allocation behavior;
- sorting;
- temporary-file and filesystem behavior;
- Parquet encoding and ZSTD compression; and
- thread scheduling across distinct pipeline phases.

The M1 Pro had only a 1.14x Geekbench single-core and 1.76x multi-core advantage
over the Windows 5350GE profile, yet the optimized staged conversion was 2.22x
faster. Its phase ratios were 1.65x for staging and 2.75x for DuckDB/Parquet.
No single Geekbench score captures that mixture.

The same-hardware 5350GE Geekbench scores were nearly equal between Windows and
Omarchy, and their later conversion totals were also nearly equal. Ubuntu,
however, was 8.6% faster overall while dividing the work differently: slower
staging and faster DuckDB/Parquet. Aggregate scores do not reveal those
phase-level tradeoffs.

## GPU score warning

Geekbench GPU compute scores are backend-specific. Keep the API in every table
and comparison:

- Metal is the native Apple GPU API used by the measured Macs.
- CUDA was the highest RTX 3090 result on Windows.
- OpenCL was available on some Windows and macOS profiles.
- Vulkan provided the direct Windows-versus-Omarchy comparison for the same
  Radeon and NVIDIA GPUs.

For example, Windows RTX 3090 CUDA scored 297,964 and Omarchy Vulkan scored
194,064. Calling Windows 53.5% faster from those values would be invalid. The
comparable Vulkan scores were 209,874 and 194,064, a 7.5% Omarchy deficit.

GPU scores also do not predict this converter's CPU-oriented path; no GPU
acceleration was used for the measured Reddit conversion.

## Practical conclusions

1. Expect OS effects to be **phase- and workload-specific**, not a universal
   percentage.
2. On the best matched Ryzen conversion, Ubuntu delivered a real but modest
   **7-9% overall advantage** over Windows.
3. The advantage came from a roughly **22% faster DuckDB/Parquet phase**, while
   Linux staging was roughly **18% slower**.
4. Omarchy and Windows were effectively tied in the later 5350GE conversion,
   but too many components changed to isolate Omarchy as the cause.
5. Same-hardware Geekbench ranged from a small Omarchy lead to a moderate
   Windows lead, depending on the machine and subtest.
6. Algorithmic fixes, especially binary block staging, had a much larger
   effect than disabling protection, changing the Windows power plan, moving
   the compressed source to a faster drive, or changing chunk size.
7. For future OS comparisons, report stage and DuckDB/Parquet time separately,
   preserve source hashes and output fingerprints, use repeated idle runs, and
   keep decoder, runtime, filesystem, thread count, memory limit, and power
   policy fixed.

## Source records

- [Conversion benchmark narrative](BENCHMARKS.md)
- [Engineering lessons](LESSONS_LEARNED.md)
- [Geekbench 7 measured shootout](benchmarks/geekbench-7-measured-shootout.json)
- [Windows optimization and M1 comparison](benchmarks/RC_2026-05-windows-optimization.json)
- [Ubuntu same-hardware comparison](benchmarks/RC_2026-05-linux-comparison.json)
- [Ubuntu native-NVMe follow-up](benchmarks/RC_2026-05-linux-nvme-chunks.json)
- [Omarchy 5350GE conversion suite](benchmarks/RC_RS_2026-05-omarchy-suite.json)
- [Omarchy 5600X thread sweep](benchmarks/RC_2026-05-lin-big-omarchy-thread-sweep.json)
- [M5 Max optimized and full-month profile](benchmarks/RC_RS_2026-05-m5-max-profile.json)

All canonical conversion outputs cited above were validated for the expected
row count and source identity. The matched Windows, Ubuntu, and M1 4M outputs
also shared content fingerprint `7365298922509737757` and passed schema and
physical sort-order checks.
