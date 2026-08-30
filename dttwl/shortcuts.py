"""Resolving a folder of shortcuts into the applications to test.

The usual way to collect the applications under test is to drop a shortcut for
each one into a single folder.  A .lnk names the shortcut, not the executable
("Adobe Photoshop 2024.lnk" points at photoshop.exe), so the target is read out
of the shortcut itself and matched against the whitelist DTT reports.
"""

import ntpath
import os
import re
import struct

HEADER_SIZE = 0x4C
HAS_LINK_TARGET_ID_LIST = 0x01
HAS_LINK_INFO = 0x02
VOLUME_ID_AND_LOCAL_BASE_PATH = 0x01

EXE_PATTERN = re.compile(rb"[ -~]{3,}?\.exe", re.IGNORECASE)


def resolve_shortcut(path):
    """Return the executable a .lnk points at, or "" when it cannot be read.

    Only the LinkInfo structure of MS-SHLLINK is parsed, which is where a
    shortcut to a local file keeps its target path.  Anything unexpected falls
    back to scanning the file for an .exe path rather than raising, since a
    shortcut that cannot be read should downgrade to name matching instead of
    stopping the scan.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return ""

    if len(data) < HEADER_SIZE or struct.unpack_from("<I", data, 0)[0] != HEADER_SIZE:
        return ""

    flags = struct.unpack_from("<I", data, 20)[0]
    offset = HEADER_SIZE

    if flags & HAS_LINK_TARGET_ID_LIST:
        if offset + 2 > len(data):
            return _scan_for_executable(data)
        id_list_size = struct.unpack_from("<H", data, offset)[0]
        offset += 2 + id_list_size

    if not flags & HAS_LINK_INFO or offset + 28 > len(data):
        return _scan_for_executable(data)

    try:
        link_info_size = struct.unpack_from("<I", data, offset)[0]
        header_size = struct.unpack_from("<I", data, offset + 4)[0]
        link_info_flags = struct.unpack_from("<I", data, offset + 8)[0]
        local_base_offset = struct.unpack_from("<I", data, offset + 16)[0]
        suffix_offset = struct.unpack_from("<I", data, offset + 24)[0]
    except struct.error:
        return _scan_for_executable(data)

    if offset + link_info_size > len(data) or not (
        link_info_flags & VOLUME_ID_AND_LOCAL_BASE_PATH
    ):
        return _scan_for_executable(data)

    unicode_offsets = header_size >= 0x24
    if unicode_offsets:
        try:
            local_base_offset = struct.unpack_from("<I", data, offset + 28)[0]
            suffix_offset = struct.unpack_from("<I", data, offset + 32)[0]
        except struct.error:
            unicode_offsets = False

    base = _read_string(data, offset + local_base_offset, unicode_offsets)
    suffix = _read_string(data, offset + suffix_offset, unicode_offsets)
    target = (base + suffix).strip()
    if target.lower().endswith(".exe"):
        return target
    return _scan_for_executable(data) or target


def _read_string(data, start, is_unicode):
    if start <= 0 or start >= len(data):
        return ""
    if is_unicode:
        end = start
        while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
            end += 2
        return data[start:end].decode("utf-16-le", "replace")
    end = data.find(b"\x00", start)
    if end == -1:
        end = len(data)
    return data[start:end].decode("mbcs" if os.name == "nt" else "latin-1", "replace")


def _scan_for_executable(data):
    """Last resort: pull an .exe path out of the raw shortcut bytes."""
    candidates = [match.group(0).decode("latin-1") for match in EXE_PATTERN.finditer(data)]
    try:
        text = data.decode("utf-16-le", "ignore")
        candidates += [m.group(0) for m in re.finditer(r"[^\x00-\x1f]{3,}?\.exe", text,
                                                       re.IGNORECASE)]
    except UnicodeDecodeError:  # pragma: no cover - decode with ignore cannot raise
        pass

    best = ""
    for candidate in candidates:
        cleaned = candidate.strip()
        if ":\\" in cleaned or "\\" in cleaned:
            if len(cleaned) > len(best):
                best = cleaned
    return best


def scan_folder(folder):
    """Map each launchable item in `folder` to the executable it starts.

    Returns [{"process_name", "path", "source"}] where `path` is what should be
    launched (the shortcut, so Windows applies its working directory and
    arguments) and `process_name` is the executable that will actually run.
    """
    entries = []
    if not os.path.isdir(folder):
        return entries

    for name in sorted(os.listdir(folder)):
        full = os.path.join(folder, name)
        if not os.path.isfile(full):
            continue
        lower = name.lower()

        if lower.endswith(".lnk"):
            target = resolve_shortcut(full)
            # ntpath, not os.path: the target is always a Windows path even
            # when the scan runs elsewhere.
            process_name = ntpath.basename(target).lower() if target else ""
            if not process_name:
                # No readable target: fall back to the shortcut's own name.
                process_name = os.path.splitext(name)[0].lower() + ".exe"
            entries.append({
                "process_name": process_name,
                "path": full,
                "source": "shortcut",
                "target": target,
            })
        elif lower.endswith(".exe"):
            entries.append({
                "process_name": lower,
                "path": full,
                "source": "executable",
                "target": full,
            })

    return entries


def match_to_whitelist(entries, whitelist_names):
    """Match scanned entries to whitelist executables.

    Exact executable name first; failing that, the stem is compared so
    "Cinebench.lnk" still lines up with cinebench.exe when the shortcut target
    could not be read.
    """
    by_name = {}
    by_stem = {}
    for entry in entries:
        name = entry["process_name"]
        by_name.setdefault(name, entry)
        by_stem.setdefault(os.path.splitext(name)[0], entry)

    matched = {}
    for wanted in whitelist_names:
        wanted = wanted.lower()
        entry = by_name.get(wanted) or by_stem.get(os.path.splitext(wanted)[0])
        if entry is not None:
            matched[wanted] = entry
    return matched
