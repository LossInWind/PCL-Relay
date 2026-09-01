from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from pathlib import Path
from typing import Optional


_CONTENTSIZE_UNKNOWN = (1 << 64) - 1
_CONTENTSIZE_ERROR = (1 << 64) - 2
_library: Optional[ctypes.CDLL] = None


def _candidates() -> list[str]:
    result = [
        os.environ.get("PCL_RELAY_ZSTD_LIBRARY", ""),
        str(Path.home() / ".local" / "share" / "pcl-codex-bridge" / "lib" / "libzstd.1.dylib"),
        str(Path.home() / ".local" / "share" / "pcl-codex-bridge" / "lib" / "libzstd.so.1"),
        "/opt/homebrew/opt/zstd/lib/libzstd.1.dylib",
        "/opt/homebrew/lib/libzstd.1.dylib",
        "/usr/local/lib/libzstd.1.dylib",
        "/usr/lib/x86_64-linux-gnu/libzstd.so.1",
        "/usr/lib/aarch64-linux-gnu/libzstd.so.1",
        "/usr/lib64/libzstd.so.1",
        "/usr/lib/libzstd.so.1",
    ]
    found = ctypes.util.find_library("zstd")
    if found:
        result.append(found)
    # A bundled PCL Relay executable lives at bridge/python/bin/python3.
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        result.extend(
            [
                str(parent / "lib" / "libzstd.1.dylib"),
                str(parent / "lib" / "libzstd.so.1"),
            ]
        )
    return list(dict.fromkeys(value for value in result if value))


def _load() -> ctypes.CDLL:
    global _library
    if _library is not None:
        return _library
    errors = []
    for candidate in _candidates():
        try:
            library = ctypes.CDLL(candidate)
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        library.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        library.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
        library.ZSTD_decompressBound.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        library.ZSTD_decompressBound.restype = ctypes.c_ulonglong
        library.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
        library.ZSTD_decompress.restype = ctypes.c_size_t
        library.ZSTD_isError.argtypes = [ctypes.c_size_t]
        library.ZSTD_isError.restype = ctypes.c_uint
        library.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
        library.ZSTD_getErrorName.restype = ctypes.c_char_p
        _library = library
        return library
    raise RuntimeError("libzstd is unavailable; install or reinstall PCL Relay")


def library_source() -> Optional[Path]:
    for candidate in _candidates():
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None


def available() -> bool:
    try:
        _load()
        return True
    except RuntimeError:
        return False


def decompress(data: bytes, max_output_bytes: int) -> bytes:
    if not data:
        raise ValueError("Empty zstd body")
    library = _load()
    source = ctypes.create_string_buffer(data)
    size = int(library.ZSTD_getFrameContentSize(source, len(data)))
    if size == _CONTENTSIZE_ERROR:
        raise ValueError("Invalid zstd frame")
    if size == _CONTENTSIZE_UNKNOWN:
        size = int(library.ZSTD_decompressBound(source, len(data)))
    if size <= 0 or size > max_output_bytes:
        raise ValueError(f"zstd body expands beyond the {max_output_bytes}-byte safety limit")
    output = ctypes.create_string_buffer(size)
    written = int(library.ZSTD_decompress(output, size, source, len(data)))
    if library.ZSTD_isError(written):
        message = library.ZSTD_getErrorName(written)
        raise ValueError((message or b"zstd decompression failed").decode("utf-8", "replace"))
    if written > max_output_bytes:
        raise ValueError(f"zstd body expands beyond the {max_output_bytes}-byte safety limit")
    return output.raw[:written]
