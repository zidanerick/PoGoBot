import struct

import pytest

from tools.gesture.check_dex import REQUIRED_CLASSES, check, defined_classes


def write_dex(path, descriptors):
    """Write the small header/tables needed to exercise the class-def reader."""
    header_size = 112
    string_ids_off = header_size
    type_ids_off = string_ids_off + len(descriptors) * 4
    class_defs_off = type_ids_off + len(descriptors) * 4
    data_off = class_defs_off + len(descriptors) * 32
    string_data = bytearray()
    string_offsets = []
    for descriptor in descriptors:
        encoded = descriptor.encode("utf-8")
        string_offsets.append(data_off + len(string_data))
        string_data.extend(bytes([len(encoded)]))
        string_data.extend(encoded + b"\0")

    data = bytearray(data_off) + string_data
    data[:8] = b"dex\n035\0"
    struct.pack_into("<I", data, 32, len(data))
    struct.pack_into("<I", data, 36, header_size)
    struct.pack_into("<I", data, 40, 0x12345678)
    struct.pack_into("<II", data, 56, len(descriptors), string_ids_off)
    struct.pack_into("<II", data, 64, len(descriptors), type_ids_off)
    struct.pack_into("<II", data, 96, len(descriptors), class_defs_off)
    for index, string_offset in enumerate(string_offsets):
        struct.pack_into("<I", data, string_ids_off + index * 4, string_offset)
        struct.pack_into("<I", data, type_ids_off + index * 4, index)
        struct.pack_into("<I", data, class_defs_off + index * 32, index)
    path.write_bytes(data)


def test_dex_check_requires_outer_and_all_nested_injector_classes(tmp_path):
    dex = tmp_path / "classes.dex"
    write_dex(dex, sorted(REQUIRED_CLASSES))

    assert defined_classes(dex) == REQUIRED_CLASSES
    check(dex)


def test_dex_check_rejects_omitted_nested_class(tmp_path):
    dex = tmp_path / "classes.dex"
    write_dex(dex, ["Lpogobot/Gesture;"])

    with pytest.raises(ValueError, match="Gesture\\$Point"):
        check(dex)
