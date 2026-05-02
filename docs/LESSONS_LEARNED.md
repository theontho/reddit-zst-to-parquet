# Project Reddit ZST to Parquet: Lessons Learned

This document captures the technical challenges and architectural decisions made during the conversion of 20+ years of Reddit archives from ZStandard JSON to Search-Optimized Parquet.

## 1. Schema Management & Evolution
*   **The "Superunion" Problem**: Reddit's schema for posts (`RS`) contains over 1,500 unique fields across history. A literal union results in massive metadata overhead.
*   **Solution: The "Clean Union"**: We used statistical JSON schemas to identify fields with >10% usage ("Useful Items"). Remaining obscure fields are captured in a single `extra_json` string column, ensuring 100% data preservation with a sane ~80-column schema.
*   **The "Edited" Flip**: One of the most common crashes. Historically, the `edited` field flipped between a Boolean (`true/false`) and a Unix Timestamp. We normalized this to `Int64`.

## 2. Type Robustness & Arrow Strictness
*   **Semi-Structured Mess**: Reddit data is often "string or dict." A field like `media_embed` might be an empty `{}` in one row and a raw string in another.
*   **Solution: Recursive Coercion**: PyArrow and DuckDB require strict typing. Our final engine uses a recursive cleaner that detects shape mismatches and automatically serializes dictionaries/lists into JSON strings when a string column is expected.
*   **Null Type Constraints**: Mapping a JSON "null" type directly to Arrow's `null` type causes crashes if any data eventually appears. We map all historically empty/null fields to `String` for future-proof robustness.

## 3. High-Performance Engine Tuning (DuckDB)
*   **Single-Thread vs. Multi-Thread**: While DuckDB can parse JSON in parallel, modern Reddit archives (2018+) are so rich that multi-threaded parsing exhausts RAM (32GB+) before disk-spilling can trigger.
*   **The "Ultra-Stable" Pattern**: For archives >5GB, we forced `threads=1` and `memory_limit='8GB'`. This slightly reduces speed but guarantees stability by forcing DuckDB's internal external-sorter to use the disk (external Volume) instead of RAM.
*   **FIFO Pipe vs. Native Decoder**: DuckDB's native ZSTD decoder crashed on frames using "long-range" compression (`--long`). We switched to a **Named Pipe (FIFO)** approach, using the system `zstd` binary for decompression, which handled every frame correctly.
*   **Pre-Sorting Chunks for Sort-Efficiency**: When converting massive (>100GB uncompressed) files, the final global `ORDER BY` during the merge step can exceed 250GB of temporary disk space (spill). By sorting each individual chunk during creation, the final merge becomes a more efficient merge-sort of already sorted streams, drastically reducing temporary disk usage.
*   **Large-Scale Merge Stability**: For merges involving >5 chunks, forcing `threads=1` and `memory_limit='16GB'` prevents resource exhaustion and ensures the sorter handles the data predictably without crashing the OS or hitting `max_temp_directory_size`.

## 4. Operational Resilience
*   **Transactional State Machine**: A simple "Completed" list is insufficient for TB-scale work. We implemented a 7-stage state machine (`pending` -> `downloading` -> `downloaded` -> `converting` -> `converted` -> `uploading` -> `completed`) recorded in an atomic JSON log.
*   **Verification Failsafes**: We established a rule: **Never delete source ZST until the Parquet is verified > 1MB.** This prevents data loss during silent conversion failures.
*   **Real-time Progress Visibility**: To avoid "black box" processing, the claim system was enhanced to update a `stage` field in the remote JSON claim. This allows monitoring progress (e.g., `downloading` -> `converting (35%)` -> `uploading`) from any machine without needing SSH access to the worker.
*   **Orphan Cleanup**: On TB-scale disks, interrupted runs leave "residue." We added a startup scanner that purges temporary directories not accounted for in the current session.

## 5. Dependency Management
*   **Implicit DuckDB Requirements**: When using DuckDB's `fetchdf()` or complex introspection, it internally relies on `pandas` and `numpy`. Always include these in the environment even if they aren't called directly in the script to avoid `ModuleNotFoundError` during runtime.

## 6. DuckDB Syntax Gotchas
*   **`read_parquet` Limitations**: Unlike `read_json`, the DuckDB `read_parquet` function does NOT support the `ignore_errors` parameter. Attempting to use it will crash the merge step.
*   **`struct_pack` for Extra Data**: When bundling unknown columns into JSON, `to_json(struct_pack(...))` is the most efficient way to preserve nested structures while maintaining a flat top-level schema.

## 7. Benchmarking & Portability
*   **Hardware Snapshotting**: By logging `cpu_brand`, `ram_gb`, and `temp_path` for every file, we can now scientifically prove the performance difference between an M1 Max vs. other machines, and External HDD vs. Internal SSD.
*   **Search Optimization**: The primary goal was search. By globally sorting every file by `author` before writing to Parquet, we transformed a 10-minute "grep" task into a 2-second DuckDB query.
