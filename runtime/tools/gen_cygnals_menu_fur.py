#!/usr/bin/env python3
import argparse
import struct
import zlib
from pathlib import Path


NOTE = {
    "C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
    "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11,
}


def midi(name: str) -> int:
    for key in ("C#", "D#", "F#", "G#", "A#"):
        if name.startswith(key):
            return (int(name[len(key):]) + 1) * 12 + NOTE[key]
    return (int(name[1:]) + 1) * 12 + NOTE[name[0]]


def put_u16(value: int) -> bytes:
    return struct.pack("<H", value)


def put_u32(value: int) -> bytes:
    return struct.pack("<I", value)


def block(tag: bytes, body: bytes) -> bytes:
    return tag + put_u32(len(body)) + body


def feature(code: bytes, payload: bytes) -> bytes:
    return code + put_u16(len(payload)) + payload


def macro(code: int, data: list[int], *, loop: int = 0xFF, speed: int = 1) -> bytes:
    return bytes([code, len(data), loop, 0xFF, 0, 0, 0, speed]) + bytes(data)


def instrument(name: str, volume: list[int], wave: int = 0) -> bytes:
    body = bytearray()
    body += put_u16(232)
    body += put_u16(0x16)
    body += feature(b"NA", name.encode("ascii") + b"\0")
    macros = bytearray()
    macros += put_u16(8)
    macros += macro(0, volume)
    macros += macro(3, [wave])
    macros += b"\xff"
    body += feature(b"MA", bytes(macros))
    body += b"EN"
    return block(b"INS2", bytes(body))


def wavetable(name: str, values: list[int]) -> bytes:
    if len(values) != 32:
        raise ValueError("WonderSwan wavetable must contain 32 4-bit samples")
    body = bytearray(name.encode("ascii") + b"\0")
    body += put_u32(32)
    body += put_u32(0)
    body += put_u32(15)
    for value in values:
        body += put_u32(max(0, min(15, value)))
    return block(b"WAVE", bytes(body))


def pat_line(note: int | None = None, inst: int | None = None, volume: int | None = None) -> bytes:
    flags = 0
    payload = bytearray()
    if note is not None:
        flags |= 1
        payload.append(note)
    if inst is not None:
        flags |= 2
        payload.append(inst)
    if volume is not None:
        flags |= 4
        payload.append(volume)
    if flags == 0:
        return b"\x80"
    return bytes([flags]) + bytes(payload)


def skip(rows: int) -> bytes:
    if rows <= 0:
        return b""
    out = bytearray()
    while rows > 0:
        chunk = min(rows, 129)
        out.append(0x80 | (chunk - 2 if chunk >= 2 else 0))
        rows -= chunk
    return bytes(out)


def pattern(channel: int, index: int, events: dict[int, tuple[int | None, int | None, int | None]], rows: int = 64) -> bytes:
    data = bytearray()
    current = 0
    for row in sorted(events):
        if row < current or row >= rows:
            continue
        data += skip(row - current)
        data += pat_line(*events[row])
        current = row + 1
    data += skip(rows - current)
    data += b"\xff"

    body = bytearray()
    body += b"\x00"
    body += bytes([channel])
    body += put_u16(index)
    body += b"\0"
    body += data
    return block(b"PATN", bytes(body))


def every(rows: list[int], notes: list[str], inst: int, volume: int) -> dict[int, tuple[int, int, int]]:
    return {row: (midi(note), inst, volume) for row, note in zip(rows, notes)}


def standard_waves() -> list[bytes]:
    return [
        wavetable("rounded saw", [0, 1, 2, 3, 5, 7, 9, 11, 13, 15, 14, 13, 11, 9, 7, 5, 3, 2, 1, 0, 1, 2, 4, 6, 8, 10, 12, 13, 12, 10, 7, 4]),
        wavetable("music box", [8, 10, 12, 14, 15, 14, 12, 10, 8, 6, 4, 2, 1, 2, 4, 6, 8, 11, 13, 15, 13, 11, 8, 5, 3, 1, 0, 1, 3, 5, 6, 7]),
        wavetable("hollow reed", [8, 13, 15, 12, 8, 4, 1, 3, 8, 13, 15, 12, 8, 4, 1, 3, 8, 11, 13, 10, 8, 6, 3, 5, 8, 11, 13, 10, 8, 6, 3, 5]),
        wavetable("dark pulse", [0, 0, 1, 2, 8, 14, 15, 15, 15, 14, 8, 2, 1, 0, 0, 0, 15, 15, 14, 13, 7, 1, 0, 0, 0, 1, 7, 13, 14, 15, 15, 15]),
    ]


def menu_parts() -> tuple[str, list[bytes], list[bytes], list[bytes], int, int, float]:
    pattern_length = 64
    instruments = [
        instrument("bright lead", [14, 13, 12, 10, 8, 6], 1),
        instrument("warm chord", [10, 9, 8, 7, 6], 2),
        instrument("sea bass", [12, 11, 10, 9], 3),
        instrument("sparkle", [13, 8, 4, 0], 1),
    ]
    patterns = [
        pattern(0, 0, every([0, 6, 12, 18, 28, 34, 44, 52], ["E7", "G7", "C8", "B7", "A7", "C8", "G7", "E7"], 0, 13), pattern_length),
        pattern(1, 0, every([0, 16, 32, 48], ["C6", "E6", "A5", "F5"], 1, 8), pattern_length),
        pattern(2, 0, every([0, 16, 32, 48], ["C5", "G4", "A4", "F4"], 2, 9), pattern_length),
        pattern(3, 0, {
            4: (midi("C8"), 3, 5), 20: (midi("E8"), 3, 4),
            36: (midi("A7"), 3, 5), 58: (midi("G7"), 3, 4),
        }, pattern_length),
    ]
    return "Lost Sea Menu Loop", instruments, standard_waves(), patterns, pattern_length, 8, 75.0


def tense_parts() -> tuple[str, list[bytes], list[bytes], list[bytes], int, int, float]:
    pattern_length = 64
    instruments = [
        instrument("thin warning", [13, 10, 8, 5, 2], 2),
        instrument("low pulse", [13, 12, 9, 6], 3),
        instrument("minor stab", [11, 7, 3, 0], 3),
        instrument("clock tick", [10, 2, 0], 1),
    ]
    patterns = [
        pattern(0, 0, every([0, 7, 15, 23, 32, 39, 47, 55], ["D7", "F7", "E7", "G#7", "D7", "F7", "C#7", "E7"], 0, 12), pattern_length),
        pattern(1, 0, {
            0: (midi("D5"), 1, 11), 6: (midi("D5"), 1, 7), 12: (midi("F5"), 1, 9),
            24: (midi("C#5"), 1, 11), 30: (midi("C#5"), 1, 7), 36: (midi("E5"), 1, 9),
            48: (midi("A4"), 1, 11), 54: (midi("G#4"), 1, 8),
        }, pattern_length),
        pattern(2, 0, every([0, 12, 24, 36, 48, 56], ["D4", "D4", "C#4", "C#4", "A3", "G#3"], 2, 9), pattern_length),
        pattern(3, 0, {
            0: (midi("D6"), 3, 5), 5: (midi("D6"), 3, 4), 13: (midi("D6"), 3, 4),
            21: (midi("C#6"), 3, 5), 29: (midi("D6"), 3, 4), 37: (midi("D#6"), 3, 4),
            45: (midi("C#6"), 3, 5), 54: (midi("D6"), 3, 5), 60: (midi("F6"), 3, 4),
        }, pattern_length),
    ]
    return "Lost Sea Tense Loop", instruments, standard_waves(), patterns, pattern_length, 6, 75.0


def investigation_parts() -> tuple[str, list[bytes], list[bytes], list[bytes], int, int, float]:
    pattern_length = 64
    instruments = [
        instrument("search lead", [11, 9, 7, 5], 2),
        instrument("question chord", [9, 8, 7, 6], 0),
        instrument("soft bass", [10, 8, 6, 4], 3),
        instrument("clue ping", [13, 8, 3, 0], 1),
    ]
    patterns = [
        pattern(0, 0, every([0, 10, 18, 30, 42, 50, 58], ["G6", "A#6", "C7", "A6", "F6", "G6", "D7"], 0, 10), pattern_length),
        pattern(1, 0, every([0, 14, 32, 46], ["D5", "F5", "C5", "D#5"], 1, 8), pattern_length),
        pattern(2, 0, every([0, 16, 28, 44, 56], ["G4", "D4", "F4", "C4", "D4"], 2, 8), pattern_length),
        pattern(3, 0, every([7, 23, 39, 55], ["G7", "A#7", "A7", "F#7"], 3, 7), pattern_length),
    ]
    return "Lost Sea Investigation Loop", instruments, standard_waves(), patterns, pattern_length, 9, 75.0


def calm_parts() -> tuple[str, list[bytes], list[bytes], list[bytes], int, int, float]:
    pattern_length = 64
    instruments = [
        instrument("gentle lead", [12, 11, 9, 7, 5], 1),
        instrument("warm pad", [8, 8, 7, 6, 5], 2),
        instrument("low tide", [9, 8, 7, 6], 0),
        instrument("soft sparkle", [10, 6, 2, 0], 1),
    ]
    patterns = [
        pattern(0, 0, every([0, 12, 24, 36, 48, 56], ["C7", "E7", "G7", "E7", "A6", "G6"], 0, 9), pattern_length),
        pattern(1, 0, every([0, 24, 40], ["C5", "A4", "F4"], 1, 6), pattern_length),
        pattern(2, 0, every([0, 24, 40], ["C4", "A3", "F3"], 2, 6), pattern_length),
        pattern(3, 0, every([18, 34, 52], ["E7", "C8", "G7"], 3, 4), pattern_length),
    ]
    return "Lost Sea Calm Loop", instruments, standard_waves(), patterns, pattern_length, 12, 75.0


MOODS = {
    "menu": menu_parts,
    "tense": tense_parts,
    "investigation": investigation_parts,
    "calm": calm_parts,
}


def build_song(mood: str) -> bytes:
    song_name, instruments, waves, patterns, pattern_length, speed, ticks_per_second = MOODS[mood]()
    orders_length = 1

    info = bytearray()
    info += bytes([0, speed, speed, 1])
    info += struct.pack("<f", ticks_per_second)
    info += put_u16(pattern_length)
    info += put_u16(orders_length)
    info += bytes([4, 16])
    info += put_u16(len(instruments))
    info += put_u16(len(waves))
    info += put_u16(0)
    info += put_u32(len(patterns))
    info += bytes([0x96]) + bytes(31)
    info += bytes(32)
    info += bytes(32)
    info += bytes(128)
    info += song_name.encode("ascii") + b"\0"
    info += b"OpenAI Codex\0"
    info += bytes(24)

    header_len = 24
    info_pointer = 0x20
    info_block_stub_len = 8 + len(info)
    pointer_count = len(instruments) + len(waves) + len(patterns)
    order_table_len = 4 * orders_length
    data_start = info_pointer + info_block_stub_len + pointer_count * 4 + order_table_len

    chunks = instruments + waves + patterns
    offsets = []
    cursor = data_start
    for chunk in chunks:
        offsets.append(cursor)
        cursor += len(chunk)

    for offset in offsets:
        info += put_u32(offset)

    for _channel in range(4):
        info += bytes([0])

    raw = bytearray()
    raw += b"-Furnace module-"
    raw += put_u32(232)
    raw += put_u32(info_pointer)
    raw += bytes(info_pointer - len(raw))
    raw += block(b"INFO", bytes(info))
    for chunk in chunks:
        raw += chunk
    return zlib.compress(bytes(raw), level=9)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--mood", choices=sorted(MOODS), default="menu")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(build_song(args.mood))


if __name__ == "__main__":
    main()
