import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from core import config
from core.utils import format_size
from transfer.base_transfer import TransferHandler
from transfer.ftp_transfer import FtpTransferHandler
from transfer.local_transfer import LocalTransferHandler
from transfer.nfs_transfer import NfsTransferHandler
from transfer.rsync_ssh_transfer import RsyncSshTransferHandler


class ManifestDownloader:
    def __init__(self, manifest_names, transfer_handler, max_workers=5):
        self.manifest_names = manifest_names
        self.transfer_handler = transfer_handler
        self.results = []
        self.lock = threading.Lock()
        self.total = len(manifest_names)
        self.processed = 0
        self.max_workers = max_workers

    def run(self):
        if not self.manifest_names:
            return

        chunk_size = (len(self.manifest_names) + self.max_workers - 1) // self.max_workers
        chunks = [self.manifest_names[i : i + chunk_size] for i in range(0, len(self.manifest_names), chunk_size)]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            executor.map(self.worker, chunks)

    def worker(self, chunk):
        if not chunk:
            return

        for name in chunk:
            try:
                content = self.transfer_handler.download_to_string(name)
                if content:
                    data = json.loads(content)
                    with self.lock:
                        self.results.append(data)
            except Exception:
                pass

            with self.lock:
                self.processed += 1
                if self.processed % 50 == 0 or self.processed == self.total:
                    print(
                        f"   Progress: {self.processed}/{self.total} manifests audited...",
                        end="\r",
                    )


def run_fleet_report():
    """Generates a comprehensive report on the progress and performance of the conversion fleet."""
    print("=" * 60)
    print("REDDIT ZST TO PARQUET: FLEET PROGRESS REPORT")
    print(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Initialize transfer handler based on config
    method = config.TRANSFER_METHOD.lower()
    handler: TransferHandler
    if method == "ftp":
        handler = FtpTransferHandler()
    elif method == "rsync":
        handler = RsyncSshTransferHandler()
    elif method == "nfs":
        handler = NfsTransferHandler()
    elif method == "local":
        handler = LocalTransferHandler()
    else:
        print(f"Error: Unsupported transfer method for report: {method}")
        return

    print(f"\n[1/5] Connecting via {method.upper()} and auditing archive...")
    zst_list, _parquet, other = handler.list_remote_files()

    # 1. Archive Audit (Weighted)
    zst_sizes = dict(zst_list)
    all_manifest_files = [f for f in other if f.endswith(".manifest.json")]
    manifest_names = {f.replace(".parquet.manifest.json", ".zst").replace("new-", "") for f in all_manifest_files}

    total_bytes = sum(zst_sizes.values())
    if total_bytes == 0:
        print("No data found to report on.")
        return

    done_bytes = sum(size for name, size in zst_sizes.items() if name in manifest_names)
    pending_bytes = total_bytes - done_bytes

    total_files = len(zst_sizes)
    done_files = len(manifest_names.intersection(zst_sizes.keys()))
    total_files - done_files

    print("\n--- ARCHIVE STATUS (BY VOLUME) ---")
    print(f"Total Data:     {format_size(total_bytes)}")
    print(f"Completed:      {format_size(done_bytes)} ({(done_bytes / total_bytes * 100):.1f}%)")
    print(f"Pending:        {format_size(pending_bytes)} ({(pending_bytes / total_bytes * 100):.1f}%)")
    print(f"File Count:     {done_files} / {total_files} ({(done_files / total_files * 100):.1f}%)")

    # 2. Timeline Progress
    print("\n[2/5] Calculating completion timeline...")

    timeline: dict[str, dict[int, dict[int, bool]]] = {"RC": {}, "RS": {}}
    pattern = re.compile(r"^(RC|RS)_(\d{4})-(\d{2})\.zst$")

    for filename in zst_sizes:
        match = pattern.match(filename)
        if match:
            ctype, year, month = match.groups()
            year = int(year)
            month = int(month)
            if year not in timeline[ctype]:
                timeline[ctype][year] = {}
            timeline[ctype][year][month] = filename in manifest_names

    for ctype in ["RC", "RS"]:
        print(f"\n--- {ctype} TIMELINE (Jan -> Dec) ---")
        years = sorted(timeline[ctype].keys())
        if not years:
            print("No data found.")
            continue

        for year in years:
            months_str = ""
            for m in range(1, 13):
                if m in timeline[ctype][year]:
                    months_str += "X " if timeline[ctype][year][m] else ". "
                else:
                    months_str += "- "
            print(f"{year}: [{months_str.strip()}]")

    # 3. Performance Auditing
    print("\n[3/5] Auditing performance from ALL manifests...")
    manifest_to_audit = sorted(all_manifest_files, reverse=True)
    downloader = ManifestDownloader(manifest_to_audit, handler, max_workers=5)
    downloader.run()
    print()

    machine_stats = {}
    ip_to_machine = {}
    for data in downloader.results:
        history = data.get("conversion_history", {})
        if not history:
            continue

        history_meta = history.get("machine", {})
        machine = history_meta.get("machine", "Unknown")
        ip = history_meta.get("ip", "Unknown")

        if machine != "Unknown" and ip != "Unknown":
            ip_to_machine[ip] = machine

        if machine not in machine_stats:
            machine_stats[machine] = {
                "count": 0,
                "total_conv_duration_sec": 0,
                "total_zst_bytes_conv": 0,
                "total_dl_duration_sec": 0,
                "total_zst_bytes_dl": 0,
                "total_up_duration_sec": 0,
                "total_parquet_bytes_up": 0,
                "total_processing_time": 0,
            }

        stats = machine_stats[machine]
        stats["count"] += 1

        parquet_name = data.get("filename", "")
        zst_name = parquet_name.replace("new-", "").replace(".parquet", ".zst")
        zst_size = zst_sizes.get(zst_name, 0)
        parquet_size = data.get("file_size", 0)

        stages = history.get("stages", {})
        dl = stages.get("download", {})
        dl_dur = dl.get("duration_sec", 0)
        if dl_dur > 0:
            stats["total_dl_duration_sec"] += dl_dur
            stats["total_zst_bytes_dl"] += zst_size

        up = stages.get("upload", {})
        up_dur = up.get("duration_sec", 0)
        if up_dur > 0:
            stats["total_up_duration_sec"] += up_dur
            stats["total_parquet_bytes_up"] += parquet_size

        conv = stages.get("conversion", {})
        conv_dur = conv.get("duration_sec", 0)
        if conv_dur and zst_size > 0:
            stats["total_conv_duration_sec"] += conv_dur
            stats["total_zst_bytes_conv"] += zst_size

        stats["total_processing_time"] += dl_dur + conv_dur + up_dur

    if machine_stats:
        print("\n--- MACHINE PERFORMANCE SUMMARY (Complete Audit) ---")
        header = f"{'Machine':<25} | {'DL 1GB':<16} | {'Conv 1GB':<16} | {'UP 1GB':<16} | {'Cycle 1GB':<8} | {'Cycle GB/min':<10}"
        print(header)
        print("-" * len(header))
        for m, s in sorted(machine_stats.items()):
            gb_bytes = 1024 * 1024 * 1024

            def get_stats(dur, size):
                if size <= 0 or dur <= 0:
                    return 0, 0
                sec_1gb = dur / (size / gb_bytes)
                mb_s = (size / (1024 * 1024)) / dur if dur > 0 else 0
                return sec_1gb, mb_s

            dl_1gb, dl_mb_s = get_stats(s["total_dl_duration_sec"], s["total_zst_bytes_dl"])
            conv_1gb, conv_mb_s = get_stats(s["total_conv_duration_sec"], s["total_zst_bytes_conv"])
            up_1gb, up_mb_s = get_stats(s["total_up_duration_sec"], s["total_parquet_bytes_up"])
            cycle_1gb, _cycle_mb_s = get_stats(s["total_processing_time"], s["total_zst_bytes_conv"])

            cycle_gb_min = 60 / cycle_1gb if cycle_1gb > 0 else 0

            print(
                f"{m[:25]:<25} | {int(dl_1gb):>3}s ({dl_mb_s:>5.1f}MB/s) | {int(conv_1gb):>3}s ({conv_mb_s:>5.1f}MB/s) | {int(up_1gb):>3}s ({up_mb_s:>5.1f}MB/s) | {int(cycle_1gb):>6}s | {cycle_gb_min:7.2f}GB/min"
            )

    # 4. Fleet Status (Active Claims)
    print("\n[4/5] Checking active claims and cleaning up stale/ghost sessions...")
    claim_files = [f for f in other if f.endswith(".claim.json")]
    machine_all_claims: dict[str, list[dict[str, Any]]] = {}

    if claim_files:
        print("\n--- ACTIVE FLEET ---")
        for claim in sorted(claim_files):
            content = handler.download_to_string(claim)
            if content:
                try:
                    data = json.loads(content)
                    meta = data.get("machine_meta", {})
                    machine = meta.get("machine", "Unknown")
                    ip = meta.get("ip", "Unknown")

                    if ("\ufffd" in machine or "ï¿½" in machine) and ip in ip_to_machine:
                        machine = ip_to_machine[ip]

                    stage = data.get("stage", "Unknown")
                    updated_str = data.get("updated_at") or data.get("started_at", "Unknown")
                    zst_file = data.get("zst_filename", "Unknown")

                    ts = datetime.min
                    if updated_str != "Unknown":
                        try:
                            ts = datetime.fromisoformat(updated_str)
                        except:
                            pass

                    stale = ts != datetime.min and datetime.now() - ts > timedelta(hours=4)

                    if stale:
                        print(f"M: {machine:<25} | S: {stage:<15} | F: {zst_file} (STALE - DELETING)")
                        handler.delete_file(claim)
                        continue

                    if machine not in machine_all_claims:
                        machine_all_claims[machine] = []
                    machine_all_claims[machine].append(
                        {
                            "filename": claim,
                            "timestamp": ts,
                            "data": data,
                            "stage": stage,
                            "file": zst_file,
                        }
                    )
                except Exception as e:
                    print(f"M: Unknown              | Error reading claim: {claim} ({e})")

        active_machines_final = {}
        for machine, machine_claims in machine_all_claims.items():
            machine_claims.sort(key=lambda x: x["timestamp"], reverse=True)
            keep = machine_claims[0]
            active_machines_final[machine] = keep
            print(f"M: {machine:<25} | S: {keep['stage']:<15} | F: {keep['file']}")

            for ghost in machine_claims[1:]:
                print(f"M: {machine:<25} | S: {ghost['stage']:<15} | F: {ghost['file']} (GHOST - DELETING)")
                handler.delete_file(ghost["filename"])

    else:
        print("\n--- ACTIVE FLEET ---")
        print("No active claims found. Is the pipeline running?")
        active_machines_final = {}

    # 5. Completion Estimates
    print("\n[5/5] Calculating estimates...")
    total_mb_per_sec: float = 0.0

    for m_name, _ in active_machines_final.items():
        if m_name in machine_stats:
            s = machine_stats[m_name]
            if s["total_processing_time"] > 0:
                m_instance_throughput = (s["total_zst_bytes_conv"] / (1024 * 1024)) / s["total_processing_time"]
                total_mb_per_sec += float(m_instance_throughput)

    if total_mb_per_sec == 0:
        mb_per_min_fleet = 1.5 * 1024
        source = "Static Baseline"
    else:
        mb_per_min_fleet = total_mb_per_sec * 60
        source = f"Observed Fleet ({len(active_machines_final)} active nodes)"

    gb_per_min_fleet = mb_per_min_fleet / 1024

    if pending_bytes > 0:
        raw_mins = (pending_bytes / (1024 * 1024)) / mb_per_min_fleet
        buffer_mins = raw_mins * 1.25
        finish_raw = datetime.now() + timedelta(minutes=raw_mins)
        finish_buffer = datetime.now() + timedelta(minutes=buffer_mins)

        print("\n--- COMPLETION ESTIMATE ---")
        print(f"Fleet Speed:    {gb_per_min_fleet:.2f} GB/min ({source})")
        print(f"Raw ETA:        {finish_raw.strftime('%Y-%m-%d %H:%M')} ({int(raw_mins // 60)}h {int(raw_mins % 60)}m)")
        print(
            f"With Buffer:    {finish_buffer.strftime('%Y-%m-%d %H:%M')} ({int(buffer_mins // 60)}h {int(buffer_mins % 60)}m)"
        )

    print("\n" + "=" * 60)
