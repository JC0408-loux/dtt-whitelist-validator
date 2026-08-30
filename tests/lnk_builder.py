"""Builds synthetic .lnk files so shortcut parsing can be tested off Windows."""

import struct

HEADER_SIZE = 0x4C
CLSID = bytes.fromhex("0114020000000000C000000000000046")


def build_lnk(local_base_path, common_path_suffix="", id_list=b"", unicode_paths=False):
    flags = 0x02  # HasLinkInfo
    if id_list:
        flags |= 0x01

    header = struct.pack("<I", HEADER_SIZE) + CLSID + struct.pack(
        "<IIQQQIIIHHII", flags, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0
    )
    assert len(header) == HEADER_SIZE, len(header)

    body = b""
    if id_list:
        body += struct.pack("<H", len(id_list)) + id_list

    if unicode_paths:
        info_header_size = 0x24
        fixed = 36
        base_bytes = local_base_path.encode("utf-16-le") + b"\x00\x00"
        suffix_bytes = common_path_suffix.encode("utf-16-le") + b"\x00\x00"
    else:
        info_header_size = 0x1C
        fixed = 28
        base_bytes = local_base_path.encode("mbcs" if False else "latin-1") + b"\x00"
        suffix_bytes = common_path_suffix.encode("latin-1") + b"\x00"

    base_offset = fixed
    suffix_offset = base_offset + len(base_bytes)
    total = suffix_offset + len(suffix_bytes)

    if unicode_paths:
        link_info = struct.pack(
            "<IIIIIIIII", total, info_header_size, 0x01, 0, 0, 0, 0,
            base_offset, suffix_offset,
        )
    else:
        link_info = struct.pack(
            "<IIIIIII", total, info_header_size, 0x01, 0, base_offset, 0, suffix_offset
        )
    link_info += base_bytes + suffix_bytes

    return header + body + link_info
