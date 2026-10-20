#!/usr/bin/env python3
"""Validate that a DEX file defines the gesture injector and nested classes."""
from __future__ import annotations

import struct
import sys
from pathlib import Path


REQUIRED_CLASSES = {
    "Lpogobot/Gesture;",
    "Lpogobot/Gesture$Point;",
    "Lpogobot/Gesture$MotionAction;",
}


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _uleb128(data: bytes, offset: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift >= 35:
            raise ValueError("invalid ULEB128 string length")


def defined_classes(path: Path) -> set[str]:
    data = path.read_bytes()
    if len(data) < 112 or not data.startswith(b"dex\n") or data[7] != 0:
        raise ValueError("not a DEX file")
    if _u32(data, 36) != 112 or _u32(data, 40) != 0x12345678:
        raise ValueError("unsupported or malformed DEX header")
    if _u32(data, 32) != len(data):
        raise ValueError("DEX file size does not match its header")

    string_count, string_offset = _u32(data, 56), _u32(data, 60)
    type_count, type_offset = _u32(data, 64), _u32(data, 68)
    class_count, class_offset = _u32(data, 96), _u32(data, 100)
    if string_offset + string_count * 4 > len(data):
        raise ValueError("string table is outside the DEX file")
    if type_offset + type_count * 4 > len(data):
        raise ValueError("type table is outside the DEX file")
    if class_offset + class_count * 32 > len(data):
        raise ValueError("class definitions are outside the DEX file")

    strings: list[str] = []
    for index in range(string_count):
        item_offset = _u32(data, string_offset + index * 4)
        _, cursor = _uleb128(data, item_offset)  # UTF-16 length, not needed for ASCII descriptors.
        end = data.index(0, cursor)
        strings.append(data[cursor:end].decode("utf-8"))

    descriptors: list[str] = []
    for index in range(type_count):
        string_index = _u32(data, type_offset + index * 4)
        if string_index >= len(strings):
            raise ValueError("type references an invalid string index")
        descriptors.append(strings[string_index])

    result = set()
    for index in range(class_count):
        class_index = _u32(data, class_offset + index * 32)
        if class_index >= len(descriptors):
            raise ValueError("class definition references an invalid type index")
        result.add(descriptors[class_index])
    return result


def check(path: Path) -> None:
    missing = REQUIRED_CLASSES - defined_classes(path)
    if missing:
        raise ValueError("DEX is missing required gesture classes: " + ", ".join(sorted(missing)))


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]))
    except (IndexError, OSError, ValueError, struct.error) as exc:
        print(f"DEX validation failed: {exc}", file=sys.stderr)
        sys.exit(1)
    print("DEX contains all required gesture classes")
