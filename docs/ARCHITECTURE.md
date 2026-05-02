# Architecture Overview: Arctic Shift Parquet Converter

This document describes the design and components of the Arctic Shift Parquet Converter, a system built for high-throughput, distributed conversion of massive Reddit data dumps (.zst) into analytical Parquet files.

## High-Level Design

The system is designed to be **stateless and distributed**. Multiple worker nodes can point to the same remote storage (NAS via FTP, NFS, or Rsync/SSH) and coordinate work without a central database or master node.

### Core Principles

1.  **Optimistic Concurrency (The Claim System):** Nodes communicate their intent by placing small `.claim.json` files next to the source data.
2.  **Transport Abstraction:** A unified interface allows the system to operate over Local FS, FTP, NFS, or Rsync/SSH seamlessly.
3.  **Isolation:** All processing happens in ephemeral temporary directories to prevent corruption of the source or partial results.
4.  **Hardware Awareness:** The system auto-detects CPU/RAM and adjusts DuckDB resource limits and threading strategies dynamically based on the specific file size being processed.

---

## Component Breakdown

### 1. The Entry Point (`main.py`)
A unified CLI dispatcher using `argparse`. Supported subcommands:
- `run`: Starts the distributed conversion processing loop.
- `report`: Audits the archive and generates a fleet performance report.
- `bench`: Runs storage benchmarks to identify local I/O bottlenecks.
- `verify`: Checks remote Parquet files against master schemas.
- `manifests`: Manually regenerates metadata manifests for existing files.

### 2. Core Logic (`core/`)
- `processor.py`: The state machine for the conversion lifecycle.
- `config.py`: Centralized configuration management with hardware auto-detection.
- `converter.py`: Orchestrates the execution of conversion engines.
- `utils.py`: Shared utilities (hardware metadata, formatting, heartbeats).

### 3. Transfer Handlers (`transfer/`)
The `TransferHandler` base class defines a standard interface for file operations:
- `LocalTransferHandler`: Optimized for standalone use; uses **symlinks** for "downloads" to avoid disk I/O.
- `FtpTransferHandler`: Robust FTP/FTPS implementation with retry logic.
- `RsyncSshTransferHandler`: Uses rsync over SSH for high-efficiency delta transfers.

### 4. Conversion Engines (`engines/`)
- **Chunked Mode (`chunked.py`)**: Recommended for stability. Processes files in row groups.
- **Streamed Mode (`streamed.py`)**: High-speed streaming through a unix pipe (Faster but prone to OOM on large files).

### 5. Fleet Monitoring & The Claim System
Nodes avoid redundant work via `{filename}.claim.json` files stored on the remote storage. The `report` command audits these claims to:
- Identify active nodes.
- Clean up "ghost" claims from nodes that crashed.
- Calculate real-world throughput across the entire fleet.


### 5. Manifest Generation
Every conversion produces a `{filename}.parquet.manifest.json`. This manifest contains:
- Exact row counts.
- Column-level usage statistics (null counts, type mapping).
- Full conversion history (which machine converted it, how long it took, what version of the code was used).

---

## Data Flow (Multi-Node Case)

1.  **Discovery**: Node A scans the NAS via FTP and finds `RC_2020-01.zst`.
2.  **Claim**: Node A uploads `RC_2020-01.claim.json` to the NAS.
3.  **Transfer**: Node A downloads the `.zst` to its local NVMe temp drive.
4.  **Processing**: Node A runs the DuckDB pipeline, updating the `.claim.json` stage locally and syncing it to the NAS so other nodes see "Node A is 50% through sorting."
5.  **Finalization**: Node A uploads the finished `.parquet` and `.manifest.json`.
6.  **Cleanup**: Node A deletes the `.claim.json` from the NAS and its local temp files.
