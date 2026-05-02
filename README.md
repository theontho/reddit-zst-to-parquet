# Reddit ZST to Parquet

> [!WARNING]
> This toolset is currently somewhat rough around the edges. However, it worked well enough to successfully convert my entire multi-terabyte download up to 2026-03 of comments and submissions. An AI agent can probably fix any rough edges that don't work well.  If you can get an 8TB SSD drive pool that will locally connect to your computer, this will probably be a much easier thing to do than to managing a semi-flakey NAS FTP server and multiple machines doing processing on the zst files.  If that is the case I would tell your AI agent to look at [chunked_engine.py](engines/chunked_engine.py) and just let it rip in a loop with duckdb & zstandard.

A high-performance, standalone toolset for converting massive Reddit Zstandard (`.zst`) dumps into analytical Parquet files with multiple conversion machines.

I used the [arctic shift](https://github.com/ArthurHeitmann/arctic_shift) project as a source for reddit archive zst downloads and schemas, go take a look!

## Features

- **Distributed by Design**: Run multiple workers on different machines; they coordinate automatically via a "Claim Ticket" system on your NAS/storage.
- **Standalone Mode**: Easily run on a single machine using local file paths.
- **Auto-Scaling Engine**: Detects system RAM and CPU to dynamically tune DuckDB's resource limits and threading.
- **Resilient Transfers**: Supports **FTP/FTPS**, **Rsync/SSH**, **NFS**, and **Local FS** with automatic retries and size verification.
- **Rich Metadata**: Generates detailed JSON manifests for every Parquet file, including row counts and column-level usage stats.

---

## Setup & Installation

### 1. Prerequisites
Ensure you have the high-performance binaries required for decompression and database operations installed on your system:

**macOS (Homebrew):**
```bash
brew install uv zstd duckdb
```

**Linux:**
Install `zstd` and `duckdb` via your package manager and [install uv](https://github.com/astral-sh/uv).

### 2. Project Setup
```bash
# Clone the repository
git clone https://github.com/theontho/reddit-zst-to-parquet.git
cd reddit-zst-to-parquet

# Run the automated setup script
# This installs system dependencies (brew), sets up the uv environment,
# and clones the necessary 'arctic_shift' schema repository.
./setup.sh
```

### Dependencies & Schemas
This project relies on the excellent [arctic_shift](https://github.com/ArthurHeitmann/arctic_shift) repository for archive schemas and processing logic. The `setup.sh` script automatically clones this into the `deps/` folder. If you are setting up manually, ensure you clone it:
```bash
mkdir -p deps
git clone https://github.com/ArthurHeitmann/arctic_shift.git deps/arctic_shift
```

---

## Quick Start (Standalone Mode)

To run the converter on a single machine using local files:

1.  **Configure**:
    ```bash
    cp config.example.toml config.local.toml
    ```
    Edit `config.local.toml` and set `transfer.method = "local"`.

2.  **Run**:
    ```bash
    uv run reddit-zst-to-parquet run
    ```

---

## Multi-Node Configuration

For distributed processing, set `transfer.method` to `ftp`, `nfs`, or `rsync` and point all workers to the same remote directory. 

### Monitoring the Fleet
You can generate a comprehensive progress report and audit the health of the fleet from any node:
```bash
uv run reddit-zst-to-parquet report
```
This tool:
- Audits all remote manifests to calculate weighted completion.
- Generates a per-year timeline of processed data.
- Benchmarks every machine in the fleet (DL/Conv/UP speeds).
- Detects and cleans up "stale" or "ghost" claims from crashed nodes.
- Provides a completion ETA based on observed fleet-wide throughput.

---

## Project Structure

- `reddit-zst-to-parquet`: The CLI entry point with subcommands (`run`, `report`, `bench`, `verify`, `manifests`).
- `core/`: Shared logic, configuration, and processing coordination.
- `commands/`: Subcommand implementations.
- `transfer/`: Handlers for different protocols (FTP, Rsync, NFS, Local).
- `engines/`: The DuckDB conversion scripts.
- `docs/`:
    - [**ARCHITECTURE.md**](./docs/ARCHITECTURE.md): Deep dive into the system design.
    - [UNIFIED_SCHEMA.md](./docs/UNIFIED_SCHEMA.md): Documentation on the Parquet output schema.

## Requirements

- **Python**: 3.12+
- **System Tools**: `zstd`, `duckdb` (must be in PATH).
- **Dependency Manager**: [uv](https://github.com/astral-sh/uv).
