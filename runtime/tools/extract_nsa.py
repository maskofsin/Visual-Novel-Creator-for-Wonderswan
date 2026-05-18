#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bz2
from pathlib import Path


NO_COMPRESSION = 0
SPB_COMPRESSION = 1
LZSS_COMPRESSION = 2
NBZ_COMPRESSION = 4


def be16(data: bytes, off: int) -> int:
    return (data[off] << 8) | data[off + 1]


def be32(data: bytes, off: int) -> int:
    return (data[off] << 24) | (data[off + 1] << 16) | (data[off + 2] << 8) | data[off + 3]


class BitReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0
        self.mask = 0
        self.buf = 0

    def get(self, n: int) -> int:
        value = 0
        for _ in range(n):
            if self.mask == 0:
                if self.pos >= len(self.data):
                    return -1
                self.buf = self.data[self.pos]
                self.pos += 1
                self.mask = 128
            value <<= 1
            if self.buf & self.mask:
                value += 1
            self.mask >>= 1
        return value


def decode_lzss(data: bytes, original_length: int) -> bytes:
    ei = 8
    ej = 4
    p = 1
    n = 1 << ei
    f = (1 << ej) + p
    ring = bytearray(n * 2)
    r = n - f
    out = bytearray()
    bits = BitReader(data)
    while len(out) < original_length:
        flag = bits.get(1)
        if flag < 0:
            break
        if flag:
            c = bits.get(8)
            if c < 0:
                break
            out.append(c)
            ring[r] = c
            r = (r + 1) & (n - 1)
        else:
            i = bits.get(ei)
            j = bits.get(ej)
            if i < 0 or j < 0:
                break
            for k in range(j + 2):
                c = ring[(i + k) & (n - 1)]
                out.append(c)
                ring[r] = c
                r = (r + 1) & (n - 1)
                if len(out) >= original_length:
                    break
    return bytes(out)


def parse_nsa(data: bytes):
    count = be16(data, 0)
    base_offset = be32(data, 2)
    off = 6
    entries = []
    for _ in range(count):
        start = off
        while data[off] != 0:
            off += 1
        raw_name = data[start:off].decode("cp932", errors="replace")
        off += 1
        compression = data[off]
        off += 1
        rel_offset = be32(data, off)
        off += 4
        length = be32(data, off)
        off += 4
        original_length = be32(data, off)
        off += 4
        entries.append({
            "name": raw_name.replace("\\", "/"),
            "compression": compression,
            "offset": base_offset + rel_offset,
            "length": length,
            "original_length": original_length,
        })
    return entries


def decode_entry(data: bytes, entry: dict) -> bytes:
    chunk = data[entry["offset"]:entry["offset"] + entry["length"]]
    compression = entry["compression"]
    if compression == NO_COMPRESSION:
        return chunk
    if compression == LZSS_COMPRESSION:
        return decode_lzss(chunk, entry["original_length"])
    if compression == NBZ_COMPRESSION:
        # NBZ stores the original length as the first 4 bytes.
        return bz2.decompress(chunk[4:])
    if compression == SPB_COMPRESSION:
        raise ValueError("SPB-compressed images are not supported by this extractor yet")
    raise ValueError(f"unknown compression type {compression}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract NScripter .nsa archives.")
    ap.add_argument("archive", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--skip-unsupported", action="store_true")
    args = ap.parse_args()

    data = args.archive.read_bytes()
    entries = parse_nsa(data)
    if args.list:
        for entry in entries:
            print(f'{entry["compression"]}\t{entry["length"]}\t{entry["original_length"]}\t{entry["name"]}')
        return

    args.out_dir.mkdir(parents=True, exist_ok=True)
    skipped = 0
    for entry in entries:
        out_path = args.out_dir / entry["name"]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_path.write_bytes(decode_entry(data, entry))
        except ValueError as exc:
            if not args.skip_unsupported:
                raise
            skipped += 1
            print(f"[!] Skipped {entry['name']}: {exc}")
    print(f"[+] Extracted {len(entries) - skipped} files to {args.out_dir}")
    if skipped:
        print(f"[+] Skipped {skipped} unsupported files")


if __name__ == "__main__":
    main()
