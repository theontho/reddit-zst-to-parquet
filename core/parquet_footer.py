import io
import struct

import pyarrow.parquet as pq

FOOTER_PROBE_BYTES = 64 * 1024
MAX_FOOTER_BYTES = 64 * 1024 * 1024


def footer_length_from_tail(tail_bytes: bytes) -> int:
    if len(tail_bytes) < 8:
        raise ValueError("Parquet tail is too small to contain a footer")
    if tail_bytes[-4:] != b"PAR1":
        raise ValueError("Parquet footer magic bytes are missing")
    footer_length: int = struct.unpack("<I", tail_bytes[-8:-4])[0]
    return footer_length


def parquet_file_from_footer_bytes(footer_bytes: bytes) -> pq.ParquetFile:
    # PyArrow accepts a minimal Parquet file made from the magic header plus footer.
    return pq.ParquetFile(io.BytesIO(b"PAR1" + footer_bytes))


def parquet_file_from_tail(tail_bytes: bytes) -> pq.ParquetFile | None:
    footer_length = footer_length_from_tail(tail_bytes)
    required_bytes = footer_length + 8
    if required_bytes > len(tail_bytes):
        return None
    return parquet_file_from_footer_bytes(tail_bytes[-required_bytes:])
