#!/usr/bin/env python3
"""
KB1001 Splash Updater v1.0
==========================

Device-specific updater for the AUMI/KB1001 Allwinner A333 tablet.

It accepts a PNG/JPG/BMP/etc., automatically prepares the exact bootloader
BMP format, patches BOTH bootlogo.bmp and bootlogo-go.bmp into a verified copy
of the known-good bootloader_a FAT16 image, performs multiple safety checks,
reboots the verified tablet into fastboot, flashes bootloader_a, reboots, and
verifies the flashed partition hash from Android.

It also supports restoring the exact stock bootloader_a image.

The updater automatically locates/imports the exact verified stock base image
when ./base/bootloader_a-stock.img is not already present.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None
    ImageOps = None


VERSION = "1.0"

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR / "base"
RUNS_DIR = SCRIPT_DIR / "runs"
STOCK_IMG = BASE_DIR / "bootloader_a-stock.img"
MANIFEST = BASE_DIR / "manifest.json"

EXPECTED_STOCK_SHA256 = (
    "725e7f88eea5656c31d997cf68d9f92cd8108aa156970a97e2f1e93ed0cb55ae"
)
EXPECTED_STOCK_SIZE = 33_554_432
EXPECTED_ORIGINAL_LOGO_SHA256 = (
    "d65888550e4d5cfb76deed1a632021d15444c50ebfb54ae674addfc9194d6c59"
)

DEFAULT_SERIAL = "KB10015A00100429"
EXPECTED_HARDWARE = "sun65iw1p1"
EXPECTED_PLATFORM = "earth"
EXPECTED_SLOT = "_a"
EXPECTED_FASTBOOT_PRODUCT = "sunxi"
TARGET_PARTITION = "bootloader_a"

TARGET_W = 800
TARGET_H = 1332
TARGET_BPP = 24
TARGET_PIXEL_OFFSET = 54
TARGET_DIB_SIZE = 40
TARGET_COMPRESSION = 0
TARGET_XPPM = 3780
TARGET_YPPM = 3780
TARGET_PIXEL_BYTES = TARGET_W * TARGET_H * 3
TARGET_FILE_SIZE = TARGET_PIXEL_OFFSET + TARGET_PIXEL_BYTES

# The stock image has a vendor BPB quirk: it claims 128 MiB even though the
# actual GPT partition/image is 32 MiB. Linux mounts it normally. We permit
# that ONLY for the exact authenticated stock image.
EXPECTED_BPB = {
    "bytes_per_sector": 512,
    "sectors_per_cluster": 4,
    "reserved_sectors": 1,
    "num_fats": 2,
    "root_entry_count": 512,
    "fat_size_sectors": 256,
    "declared_total_sectors": 262144,
    "actual_total_sectors": 65536,
}

TARGET_NAMES = ("bootlogo.bmp", "bootlogo-go.bmp")

IS_WINDOWS = os.name == "nt"


def host_name() -> str:
    if IS_WINDOWS:
        return "Windows"
    if sys.platform == "darwin":
        return "macOS"
    return "Linux/Unix"


def _tool_names(base: str) -> list[str]:
    if IS_WINDOWS:
        return [f"{base}.exe", base]
    return [base]


def resolve_host_tool(base: str) -> str:
    """Resolve adb/fastboot from local folders first, then PATH."""
    host_subdir = "windows" if IS_WINDOWS else "linux"
    search_dirs = [
        SCRIPT_DIR,
        SCRIPT_DIR / "tools",
        SCRIPT_DIR / "tools" / host_subdir,
    ]

    for name in _tool_names(base):
        for directory in search_dirs:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate.resolve())

    for name in _tool_names(base):
        found = shutil.which(name)
        if found:
            return found

    raise SafetyError(
        f"Required host tool not found: {' / '.join(_tool_names(base))}. "
        "Install Android Platform Tools or place the executable next to the updater."
    )


class SafetyError(RuntimeError):
    pass


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(
    argv: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: int | None = None,
    stdin=None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            timeout=timeout,
            stdin=stdin,
        )
    except FileNotFoundError:
        raise SafetyError(f"Required command not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        raise SafetyError(f"Command timed out: {' '.join(argv)}")
    except subprocess.CalledProcessError as e:
        output = e.stdout or ""
        raise SafetyError(
            f"Command failed ({e.returncode}): {' '.join(argv)}\n{output}"
        )


def require_command(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise SafetyError(f"Required command is not installed: {name}")
    return p


@dataclass
class BMPInfo:
    file_size: int
    pixel_offset: int
    dib_size: int
    width: int
    height: int
    planes: int
    bpp: int
    compression: int
    image_size: int
    xppm: int
    yppm: int
    colors_used: int
    important_colors: int


def parse_bmp(data: bytes) -> BMPInfo:
    if len(data) < 54 or data[:2] != b"BM":
        raise SafetyError("Prepared logo is not a Windows BMP.")
    return BMPInfo(
        struct.unpack_from("<I", data, 2)[0],
        struct.unpack_from("<I", data, 10)[0],
        struct.unpack_from("<I", data, 14)[0],
        struct.unpack_from("<i", data, 18)[0],
        struct.unpack_from("<i", data, 22)[0],
        struct.unpack_from("<H", data, 26)[0],
        struct.unpack_from("<H", data, 28)[0],
        struct.unpack_from("<I", data, 30)[0],
        struct.unpack_from("<I", data, 34)[0],
        struct.unpack_from("<i", data, 38)[0],
        struct.unpack_from("<i", data, 42)[0],
        struct.unpack_from("<I", data, 46)[0],
        struct.unpack_from("<I", data, 50)[0],
    )


def validate_logo_bytes(data: bytes) -> BMPInfo:
    info = parse_bmp(data)
    checks = [
        (len(data) == TARGET_FILE_SIZE, f"actual size {len(data)} != {TARGET_FILE_SIZE}"),
        (info.file_size == TARGET_FILE_SIZE, f"header size {info.file_size} != {TARGET_FILE_SIZE}"),
        (info.pixel_offset == TARGET_PIXEL_OFFSET, f"pixel offset {info.pixel_offset} != 54"),
        (info.dib_size == TARGET_DIB_SIZE, f"DIB size {info.dib_size} != 40"),
        (info.width == TARGET_W, f"width {info.width} != 800"),
        (info.height == TARGET_H, f"height {info.height} != 1332"),
        (info.planes == 1, f"planes {info.planes} != 1"),
        (info.bpp == TARGET_BPP, f"BPP {info.bpp} != 24"),
        (info.compression == 0, f"compression {info.compression} != BI_RGB"),
        (info.image_size == TARGET_PIXEL_BYTES, f"pixel bytes {info.image_size} != {TARGET_PIXEL_BYTES}"),
        (info.xppm == TARGET_XPPM, f"xppm {info.xppm} != {TARGET_XPPM}"),
        (info.yppm == TARGET_YPPM, f"yppm {info.yppm} != {TARGET_YPPM}"),
        (info.colors_used == 0, f"colors_used {info.colors_used} != 0"),
        (info.important_colors == 0, f"important_colors {info.important_colors} != 0"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    if bad:
        raise SafetyError("Prepared logo failed strict validation:\n  - " + "\n  - ".join(bad))
    return info


def exact_bmp_bytes(img: "Image.Image") -> bytes:
    if img.size != (TARGET_W, TARGET_H):
        raise SafetyError(f"Internal image size error: {img.size}")
    img = img.convert("RGB")

    header = bytearray(TARGET_PIXEL_OFFSET)
    header[:2] = b"BM"
    struct.pack_into("<I", header, 2, TARGET_FILE_SIZE)
    struct.pack_into("<HH", header, 6, 0, 0)
    struct.pack_into("<I", header, 10, TARGET_PIXEL_OFFSET)
    struct.pack_into("<I", header, 14, TARGET_DIB_SIZE)
    struct.pack_into("<i", header, 18, TARGET_W)
    struct.pack_into("<i", header, 22, TARGET_H)  # bottom-up
    struct.pack_into("<H", header, 26, 1)
    struct.pack_into("<H", header, 28, TARGET_BPP)
    struct.pack_into("<I", header, 30, 0)
    struct.pack_into("<I", header, 34, TARGET_PIXEL_BYTES)
    struct.pack_into("<i", header, 38, TARGET_XPPM)
    struct.pack_into("<i", header, 42, TARGET_YPPM)
    struct.pack_into("<I", header, 46, 0)
    struct.pack_into("<I", header, 50, 0)

    raw = img.tobytes()
    stride = TARGET_W * 3
    out = bytearray(header)

    for y in range(TARGET_H - 1, -1, -1):
        row = raw[y * stride:(y + 1) * stride]
        bgr = bytearray(len(row))
        bgr[0::3] = row[2::3]
        bgr[1::3] = row[1::3]
        bgr[2::3] = row[0::3]
        out.extend(bgr)

    data = bytes(out)
    validate_logo_bytes(data)
    return data


def prepare_artwork(
    source: Path,
    bmp_out: Path,
    preview_out: Path,
    *,
    fit_mode: str,
    background: str,
) -> dict:
    if Image is None or ImageOps is None:
        raise SafetyError(
            "Pillow is required. Install it with: sudo apt install python3-pil"
        )
    if not source.is_file():
        raise SafetyError(f"Input image does not exist: {source}")

    im = Image.open(source)
    im = ImageOps.exif_transpose(im)
    im.load()
    src_size = im.size

    # Preserve alpha while composing.
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")

    target = (TARGET_W, TARGET_H)

    if fit_mode == "contain":
        work = im.convert("RGBA")
        contained = ImageOps.contain(work, target, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", target, background)
        x = (TARGET_W - contained.width) // 2
        y = (TARGET_H - contained.height) // 2
        canvas.alpha_composite(contained, (x, y))
        final = canvas.convert("RGB")
    elif fit_mode == "cover":
        final = ImageOps.fit(
            im.convert("RGB"),
            target,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    elif fit_mode == "stretch":
        final = im.convert("RGB").resize(target, Image.Resampling.LANCZOS)
    else:
        raise SafetyError(f"Unknown fit mode: {fit_mode}")

    preview_out.parent.mkdir(parents=True, exist_ok=True)
    final.save(preview_out, "PNG", optimize=True)

    bmp = exact_bmp_bytes(final)
    bmp_out.write_bytes(bmp)
    info = validate_logo_bytes(bmp)

    return {
        "input": str(source),
        "input_size": list(src_size),
        "fit_mode": fit_mode,
        "background": background,
        "prepared_size": [TARGET_W, TARGET_H],
        "bmp_sha256": sha256_bytes(bmp),
        "bmp_size": len(bmp),
        "bpp": info.bpp,
        "compression": info.compression,
        "pixel_offset": info.pixel_offset,
    }


@dataclass
class FATFile:
    short_name: str
    long_name: str | None
    first_cluster: int
    size: int
    dir_entry_offset: int

    @property
    def display_name(self) -> str:
        return self.long_name or self.short_name


@dataclass
class FAT16:
    data: bytearray
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    num_fats: int
    root_entry_count: int
    fat_size_sectors: int
    total_sectors: int
    root_start: int
    root_size: int
    fat_start: int
    data_start: int
    cluster_size: int

    @classmethod
    def parse(cls, blob: bytes | bytearray, *, allow_declared_larger=False) -> "FAT16":
        if len(blob) < 512:
            raise SafetyError("Bootloader image is too small.")
        if blob[510:512] != b"\x55\xaa":
            raise SafetyError("Missing FAT boot-sector signature 0x55AA.")
        if b"FAT16" not in bytes(blob[54:62]):
            raise SafetyError("Image does not identify as FAT16.")

        bps = struct.unpack_from("<H", blob, 11)[0]
        spc = blob[13]
        reserved = struct.unpack_from("<H", blob, 14)[0]
        nfats = blob[16]
        root_count = struct.unpack_from("<H", blob, 17)[0]
        total16 = struct.unpack_from("<H", blob, 19)[0]
        fatsz = struct.unpack_from("<H", blob, 22)[0]
        total32 = struct.unpack_from("<I", blob, 32)[0]
        total = total16 or total32

        if bps not in (512, 1024, 2048, 4096):
            raise SafetyError(f"Suspicious FAT bytes/sector: {bps}")
        if spc == 0 or (spc & (spc - 1)):
            raise SafetyError(f"Suspicious FAT sectors/cluster: {spc}")
        if nfats < 1 or fatsz == 0 or root_count == 0:
            raise SafetyError("Invalid FAT16 geometry.")

        root_sectors = ((root_count * 32) + (bps - 1)) // bps
        fat_start = reserved * bps
        root_start = (reserved + nfats * fatsz) * bps
        root_size = root_sectors * bps
        data_start = (reserved + nfats * fatsz + root_sectors) * bps
        cluster_size = bps * spc

        declared = total * bps
        if declared > len(blob) and not allow_declared_larger:
            raise SafetyError(
                f"FAT declares {declared} bytes, larger than image {len(blob)}."
            )
        if data_start >= len(blob):
            raise SafetyError("FAT data area starts outside the actual image.")

        return cls(
            bytearray(blob), bps, spc, reserved, nfats, root_count, fatsz, total,
            root_start, root_size, fat_start, data_start, cluster_size
        )

    @staticmethod
    def _decode_lfn_piece(entry: bytes) -> str:
        raw = entry[1:11] + entry[14:26] + entry[28:32]
        chars = []
        for i in range(0, len(raw), 2):
            code = struct.unpack_from("<H", raw, i)[0]
            if code in (0x0000, 0xFFFF):
                break
            chars.append(chr(code))
        return "".join(chars)

    @staticmethod
    def _short_name(entry: bytes) -> str:
        name = entry[0:8].decode("ascii", "replace").rstrip()
        ext = entry[8:11].decode("ascii", "replace").rstrip()
        if entry[0] == 0x05:
            name = chr(0xE5) + name[1:]
        return f"{name}.{ext}" if ext else name

    def root_files(self) -> list[FATFile]:
        result = []
        lfn = []
        for idx in range(self.root_entry_count):
            off = self.root_start + idx * 32
            if off + 32 > len(self.data):
                raise SafetyError("Root directory extends past actual image.")
            e = bytes(self.data[off:off + 32])
            if e[0] == 0x00:
                break
            if e[0] == 0xE5:
                lfn.clear()
                continue
            attr = e[11]
            if attr == 0x0F:
                lfn.append((e[0] & 0x1F, self._decode_lfn_piece(e)))
                continue
            if attr & 0x08:
                lfn.clear()
                continue
            long_name = "".join(x for _, x in sorted(lfn)) if lfn else None
            lfn.clear()
            if attr & 0x10:
                continue
            result.append(
                FATFile(
                    self._short_name(e),
                    long_name,
                    struct.unpack_from("<H", e, 26)[0],
                    struct.unpack_from("<I", e, 28)[0],
                    off,
                )
            )
        return result

    def fat_entry(self, cluster: int) -> int:
        off = self.fat_start + cluster * 2
        if off + 2 > len(self.data):
            raise SafetyError("FAT lookup outside actual image.")
        return struct.unpack_from("<H", self.data, off)[0]

    def chain(self, first_cluster: int) -> list[int]:
        if first_cluster < 2:
            raise SafetyError(f"Invalid first cluster {first_cluster}.")
        max_clusters = max(1, (len(self.data) - self.data_start) // self.cluster_size)
        seen = set()
        chain = []
        c = first_cluster
        while True:
            if c in seen:
                raise SafetyError(f"FAT loop at cluster {c}.")
            if c < 2 or c >= max_clusters + 2:
                raise SafetyError(f"Cluster {c} is outside the actual image.")
            seen.add(c)
            chain.append(c)
            nxt = self.fat_entry(c)
            if nxt >= 0xFFF8:
                break
            if nxt == 0xFFF7:
                raise SafetyError(f"Bad FAT cluster after {c}.")
            if nxt in (0, 1):
                raise SafetyError(f"Unexpected FAT entry 0x{nxt:04x} after {c}.")
            c = nxt
        return chain

    def cluster_offset(self, cluster: int) -> int:
        off = self.data_start + (cluster - 2) * self.cluster_size
        if off < 0 or off >= len(self.data):
            raise SafetyError(f"Cluster {cluster} maps outside actual image.")
        return off

    def find_root_file(self, wanted: str) -> FATFile:
        wanted = wanted.lower()
        for f in self.root_files():
            names = {f.short_name.lower()}
            if f.long_name:
                names.add(f.long_name.lower())
            if wanted in names:
                return f
        if wanted == "bootlogo-go.bmp":
            for f in self.root_files():
                if (
                    f.short_name.lower().startswith("bootlo~")
                    and f.short_name.lower().endswith(".bmp")
                    and f.size == TARGET_FILE_SIZE
                ):
                    return f
        raise SafetyError(f"Could not locate {wanted} in FAT root.")

    def read_file(self, f: FATFile) -> bytes:
        remaining = f.size
        out = bytearray()
        for c in self.chain(f.first_cluster):
            off = self.cluster_offset(c)
            take = min(remaining, self.cluster_size)
            if off + take > len(self.data):
                raise SafetyError(f"{f.display_name} extends past actual image.")
            out.extend(self.data[off:off + take])
            remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise SafetyError(f"FAT chain too short for {f.display_name}.")
        return bytes(out)

    def file_extents(self, f: FATFile) -> list[tuple[int, int]]:
        remaining = f.size
        out = []
        for c in self.chain(f.first_cluster):
            take = min(remaining, self.cluster_size)
            off = self.cluster_offset(c)
            if off + take > len(self.data):
                raise SafetyError(f"{f.display_name} extent past actual image.")
            out.append((off, off + take))
            remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise SafetyError(f"FAT chain too short for {f.display_name}.")
        return out

    def overwrite_same_size(self, f: FATFile, replacement: bytes) -> None:
        if len(replacement) != f.size:
            raise SafetyError(
                f"Replacement size {len(replacement)} != existing {f.size} for {f.display_name}."
            )
        pos = 0
        for c in self.chain(f.first_cluster):
            if pos >= len(replacement):
                break
            off = self.cluster_offset(c)
            take = min(self.cluster_size, len(replacement) - pos)
            if off + take > len(self.data):
                raise SafetyError("Write would extend past actual image.")
            self.data[off:off + take] = replacement[pos:pos + take]
            pos += take
        if pos != len(replacement):
            raise SafetyError(f"Incomplete replacement of {f.display_name}.")


def stock_base_candidates() -> list[Path]:
    """Possible locations of the exact known-good stock bootloader image."""
    candidates = [
        STOCK_IMG,
        SCRIPT_DIR / "bootloader_a-stock.img",
        SCRIPT_DIR / "bootloader_a-current.img",
        SCRIPT_DIR.parent / "bootloader_a-current.img",
        Path.cwd() / "bootloader_a-current.img",
    ]

    if IS_WINDOWS:
        home = Path.home()
        candidates.extend([
            home / "Downloads" / "bootloader_a-current.img",
            home / "Desktop" / "bootloader_a-current.img",
            Path("C:/KB1001/bootloader_a-current.img"),
        ])
    result = []
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def is_exact_stock_image(path: Path) -> bool:
    try:
        return (
            path.is_file()
            and path.stat().st_size == EXPECTED_STOCK_SIZE
            and sha256_file(path) == EXPECTED_STOCK_SHA256
        )
    except OSError:
        return False


def ensure_stock_base() -> Path:
    """
    Keep the updater self-contained without a separate setup program.
    If the canonical base is missing, import an exact verified local copy.
    """
    if is_exact_stock_image(STOCK_IMG):
        return STOCK_IMG

    if STOCK_IMG.exists():
        raise SafetyError(
            f"{STOCK_IMG} exists but does not match the verified stock image.\n"
            f"Expected size: {EXPECTED_STOCK_SIZE}\n"
            f"Expected SHA256: {EXPECTED_STOCK_SHA256}"
        )

    for candidate in stock_base_candidates():
        if candidate == STOCK_IMG:
            continue
        if is_exact_stock_image(candidate):
            BASE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = STOCK_IMG.with_name(STOCK_IMG.name + ".tmp")
            shutil.copy2(candidate, tmp)
            if not is_exact_stock_image(tmp):
                tmp.unlink(missing_ok=True)
                raise SafetyError("Automatic stock-base import failed verification.")
            tmp.replace(STOCK_IMG)
            print("Imported verified stock base automatically:")
            print(f"  {candidate}")
            print(f"    -> {STOCK_IMG}")
            return STOCK_IMG

    raise SafetyError(
        "Verified stock bootloader image not found.\n"
        f"Place the exact stock image at:\n  {STOCK_IMG}\n"
        "or place bootloader_a-current.img next to the updater (or its parent).\n"
        f"Required size: {EXPECTED_STOCK_SIZE}\n"
        f"Required SHA256: {EXPECTED_STOCK_SHA256}"
    )


def verify_stock_base() -> tuple[bytes, FAT16]:
    ensure_stock_base()

    size = STOCK_IMG.stat().st_size
    digest = sha256_file(STOCK_IMG)
    if size != EXPECTED_STOCK_SIZE:
        raise SafetyError(f"Stock base size mismatch: {size} != {EXPECTED_STOCK_SIZE}")
    if digest != EXPECTED_STOCK_SHA256:
        raise SafetyError(
            "Stock base SHA-256 mismatch.\n"
            f"Expected: {EXPECTED_STOCK_SHA256}\n"
            f"Got:      {digest}"
        )

    blob = STOCK_IMG.read_bytes()
    fat = FAT16.parse(blob, allow_declared_larger=True)

    geometry = {
        "bytes_per_sector": fat.bytes_per_sector,
        "sectors_per_cluster": fat.sectors_per_cluster,
        "reserved_sectors": fat.reserved_sectors,
        "num_fats": fat.num_fats,
        "root_entry_count": fat.root_entry_count,
        "fat_size_sectors": fat.fat_size_sectors,
        "declared_total_sectors": fat.total_sectors,
        "actual_total_sectors": len(blob) // fat.bytes_per_sector,
    }
    if geometry != EXPECTED_BPB:
        raise SafetyError(f"Authenticated stock image BPB geometry mismatch: {geometry!r}")

    for name in TARGET_NAMES:
        entry = fat.find_root_file(name)
        if entry.size != TARGET_FILE_SIZE:
            raise SafetyError(f"Stock {name} size mismatch: {entry.size}")
        logo = fat.read_file(entry)
        validate_logo_bytes(logo)
        logo_hash = sha256_bytes(logo)
        if logo_hash != EXPECTED_ORIGINAL_LOGO_SHA256:
            raise SafetyError(
                f"Stock {name} SHA-256 mismatch.\n"
                f"Expected {EXPECTED_ORIGINAL_LOGO_SHA256}\n"
                f"Got {logo_hash}"
            )

    return blob, fat

def diff_ranges(a: bytes, b: bytes) -> list[tuple[int, int]]:
    if len(a) != len(b):
        raise SafetyError("Patch changed bootloader image length.")
    ranges = []
    start = None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y and start is None:
            start = i
        elif x == y and start is not None:
            ranges.append((start, i))
            start = None
    if start is not None:
        ranges.append((start, len(a)))
    return ranges


def range_covered(extents: list[tuple[int, int]], start: int, end: int) -> bool:
    pos = start
    for a, b in sorted(extents):
        if b <= pos:
            continue
        if a > pos:
            return False
        pos = max(pos, b)
        if pos >= end:
            return True
    return pos >= end


def fsck_signature(path: Path) -> tuple[int, str] | None:
    fsck = shutil.which("fsck.fat") or shutil.which("dosfsck")
    if not fsck:
        return None
    p = subprocess.run(
        [fsck, "-n", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    normalized = p.stdout.replace(str(path), "<IMAGE>")
    return p.returncode, normalized


def build_patched_image(
    replacement_bootlogo_bmp: Path,
    output_img: Path,
    replacement_bootlogo_go_bmp: Path | None = None,
) -> dict:
    """
    Patch the two bootloader splash slots.

    If replacement_bootlogo_go_bmp is omitted, the first image is written to
    BOTH bootlogo.bmp and bootlogo-go.bmp (legacy/single-image behavior).

    If supplied, bootlogo.bmp receives the first image and bootlogo-go.bmp
    receives the second image.
    """
    if replacement_bootlogo_go_bmp is None:
        replacement_bootlogo_go_bmp = replacement_bootlogo_bmp

    replacements = {
        "bootlogo.bmp": replacement_bootlogo_bmp.read_bytes(),
        "bootlogo-go.bmp": replacement_bootlogo_go_bmp.read_bytes(),
    }

    for name, data in replacements.items():
        validate_logo_bytes(data)

    original, fat = verify_stock_base()
    targets = {name: fat.find_root_file(name) for name in TARGET_NAMES}
    extents = []
    original_hashes = {}

    for name, entry in targets.items():
        existing = fat.read_file(entry)
        original_hashes[name] = sha256_bytes(existing)
        extents.extend(fat.file_extents(entry))

    for name, entry in targets.items():
        fat.overwrite_same_size(entry, replacements[name])

    patched = bytes(fat.data)
    changes = diff_ranges(original, patched)
    for start, end in changes:
        if not range_covered(extents, start, end):
            raise SafetyError(
                f"Changed byte range 0x{start:x}-0x{end:x} is outside target logo extents."
            )

    verify_fat = FAT16.parse(patched, allow_declared_larger=True)
    final_logo_hashes = {}
    for name in TARGET_NAMES:
        entry = verify_fat.find_root_file(name)
        extracted = verify_fat.read_file(entry)
        validate_logo_bytes(extracted)
        if extracted != replacements[name]:
            raise SafetyError(f"Post-patch re-extraction mismatch for {name}.")
        final_logo_hashes[name] = sha256_bytes(extracted)

    output_img.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_img.with_name(output_img.name + ".tmp")
    tmp.write_bytes(patched)
    if tmp.read_bytes() != patched:
        tmp.unlink(missing_ok=True)
        raise SafetyError("Local output write verification failed.")
    tmp.replace(output_img)

    source_fsck = fsck_signature(STOCK_IMG)
    patched_fsck = fsck_signature(output_img)
    if source_fsck is not None or patched_fsck is not None:
        if source_fsck != patched_fsck:
            raise SafetyError(
                "Read-only FAT fsck result differs between stock and patched images."
            )
        fsck_result = f"PASS identical to stock (exit={source_fsck[0]})"
    else:
        fsck_result = "SKIPPED (fsck.fat not installed)"

    result = {
        "stock_sha256": EXPECTED_STOCK_SHA256,
        "bootlogo_source_bmp_sha256": sha256_bytes(replacements["bootlogo.bmp"]),
        "bootlogo_go_source_bmp_sha256": sha256_bytes(replacements["bootlogo-go.bmp"]),
        "output_sha256": sha256_file(output_img),
        "output_size": output_img.stat().st_size,
        "changed_ranges": len(changes),
        "changed_bytes_confined_to_logo_extents": True,
        "bootlogo_hashes": final_logo_hashes,
        "fsck": fsck_result,
    }
    if result["output_size"] != EXPECTED_STOCK_SIZE:
        raise SafetyError("Output image size changed.")
    return result

def adb_devices() -> list[str]:
    adb = resolve_host_tool("adb")
    out = run([adb, "devices"]).stdout
    devices = []
    for line in out.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 2 and cols[1] == "device":
            devices.append(cols[0])
    return devices


def adb_shell(serial: str, command: str, *, timeout=20) -> str:
    adb = resolve_host_tool("adb")
    return run([adb, "-s", serial, "shell", command], timeout=timeout).stdout.strip()

def verify_android_device(serial: str) -> dict:
    devices = adb_devices()
    if serial not in devices:
        raise SafetyError(
            f"Expected ADB device {serial} is not connected.\nConnected: {devices}"
        )
    if len(devices) != 1:
        raise SafetyError(
            f"Refusing with multiple ADB devices connected: {devices}"
        )

    root_id = adb_shell(serial, "su -c id")
    if "uid=0(root)" not in root_id:
        raise SafetyError("Magisk/root shell check failed.")

    hardware = adb_shell(serial, "getprop ro.hardware")
    platform = adb_shell(serial, "getprop ro.board.platform")
    slot = adb_shell(serial, "getprop ro.boot.slot_suffix")

    if hardware != EXPECTED_HARDWARE:
        raise SafetyError(f"Unexpected ro.hardware: {hardware}")
    if platform != EXPECTED_PLATFORM:
        raise SafetyError(f"Unexpected ro.board.platform: {platform}")
    if slot != EXPECTED_SLOT:
        raise SafetyError(f"Unexpected active slot: {slot}")

    return {
        "serial": serial,
        "hardware": hardware,
        "platform": platform,
        "slot": slot,
        "root": True,
    }


class FastbootRunner:
    def __init__(self, serial: str):
        self.serial = serial
        self.fastboot = resolve_host_tool("fastboot")
        self.prefix = [self.fastboot]

    @staticmethod
    def list_devices(prefix: list[str]) -> list[str]:
        try:
            proc = subprocess.run(
                prefix + ["devices"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise SafetyError(f"fastboot executable not found: {prefix[0]}")

        return [
            line.split()[0]
            for line in proc.stdout.splitlines()
            if line.split()
        ]

    def cmd(self, *args: str, timeout=180) -> str:
        argv = self.prefix + ["-s", self.serial] + list(args)
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise SafetyError(
                f"fastboot command failed ({proc.returncode}): {' '.join(argv)}\n"
                f"{proc.stdout}"
            )
        return proc.stdout

    def getvar(self, name: str) -> str:
        out = self.cmd("getvar", name, timeout=30)
        for line in out.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                if key.strip().lower().endswith(name.lower()):
                    return value.strip()
        return out.strip()


def wait_for_fastboot(serial: str, timeout=60) -> FastbootRunner:
    """
    Windows uses fastboot.exe directly.
    Linux/Unix tries fastboot directly, then sudo fastboot if required.
    """
    deadline = time.time() + timeout
    fastboot = resolve_host_tool("fastboot")

    if IS_WINDOWS:
        while time.time() < deadline:
            devices = FastbootRunner.list_devices([fastboot])
            if serial in devices:
                if len(devices) != 1:
                    raise SafetyError(f"Refusing with multiple fastboot devices: {devices}")
                runner = FastbootRunner(serial)
                runner.prefix = [fastboot]
                return runner
            time.sleep(2)

        raise SafetyError(
            f"Timed out waiting for fastboot.exe device {serial}. "
            "Check/install the Windows Android Bootloader/WinUSB driver."
        )

    direct_deadline = min(deadline, time.time() + 8)
    while time.time() < direct_deadline:
        devices = FastbootRunner.list_devices([fastboot])
        if serial in devices:
            if len(devices) != 1:
                raise SafetyError(f"Refusing with multiple fastboot devices: {devices}")
            runner = FastbootRunner(serial)
            runner.prefix = [fastboot]
            return runner
        time.sleep(1)

    sudo = shutil.which("sudo")
    if sudo:
        print("fastboot needs elevated USB access; requesting sudo authentication...")
        subprocess.run(["sudo", "-v"], check=True)

        while time.time() < deadline:
            prefix = ["sudo", "-n", fastboot]
            devices = FastbootRunner.list_devices(prefix)
            if serial in devices:
                if len(devices) != 1:
                    raise SafetyError(f"Refusing with multiple fastboot devices: {devices}")
                runner = FastbootRunner(serial)
                runner.prefix = prefix
                return runner
            time.sleep(2)

    raise SafetyError(f"Timed out waiting for fastboot device {serial}.")

def wait_for_adb(serial: str, timeout=150) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if serial in adb_devices():
                return
        except Exception:
            pass
        time.sleep(2)
    raise SafetyError("Timed out waiting for Android/ADB after reboot.")


def verify_partition_hash(serial: str, expected: str, timeout=90) -> str:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            last = adb_shell(
                serial,
                f"su -c 'sha256sum /dev/block/by-name/{TARGET_PARTITION}'",
                timeout=15,
            )
            got = last.split()[0] if last else ""
            if got == expected:
                return got
        except Exception:
            pass
        time.sleep(2)
    raise SafetyError(
        "Post-flash partition hash verification failed.\n"
        f"Expected: {expected}\nLast output: {last}"
    )


def flash_image(serial: str, image: Path, expected_hash: str) -> dict:
    if sha256_file(image) != expected_hash:
        raise SafetyError("Local image hash changed before flashing.")

    verify_android_device(serial)

    print(f"Rebooting {serial} into bootloader...")
    adb = resolve_host_tool("adb")
    run([adb, "-s", serial, "reboot", "bootloader"], timeout=20)

    fb = wait_for_fastboot(serial, timeout=60)
    product = fb.getvar("product")
    if EXPECTED_FASTBOOT_PRODUCT not in product:
        raise SafetyError(f"Unexpected fastboot product response: {product}")

    print(f"Flashing {TARGET_PARTITION}...")
    flash_out = fb.cmd("flash", TARGET_PARTITION, str(image), timeout=240)
    print(flash_out.rstrip())

    # Only reboot after the flash command reports success.
    print("Flash command succeeded; rebooting Android...")
    reboot_out = fb.cmd("reboot", timeout=30)
    if reboot_out.strip():
        print(reboot_out.rstrip())

    wait_for_adb(serial, timeout=180)
    flashed_hash = verify_partition_hash(serial, expected_hash, timeout=120)
    return {
        "partition": TARGET_PARTITION,
        "verified_sha256": flashed_hash,
        "post_flash_verification": "PASS",
    }


def create_run_dir() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    d = RUNS_DIR / now_stamp()
    suffix = 1
    while d.exists():
        d = RUNS_DIR / f"{now_stamp()}-{suffix}"
        suffix += 1
    d.mkdir()
    return d


def write_report(run_dir: Path, data: dict) -> Path:
    p = run_dir / "verification.json"
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return p


def confirm_flash(message: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    print()
    print(message)
    answer = input("Type FLASH to continue: ").strip()
    if answer != "FLASH":
        raise SafetyError("Cancelled; nothing was flashed.")


def cmd_update(args) -> int:
    images = list(args.images)
    if not 1 <= len(images) <= 2:
        raise SafetyError("Update accepts exactly one or two source images.")

    for image in images:
        if not image.is_file():
            raise SafetyError(f"Input image does not exist: {image}")

    verify_stock_base()
    verify_android_device(args.serial)

    run_dir = create_run_dir()
    custom = run_dir / "bootloader_a-custom.img"

    if len(images) == 1:
        # One image: write the same prepared artwork into BOTH stock logo slots.
        input1 = run_dir / ("input" + images[0].suffix.lower())
        shutil.copy2(images[0], input1)

        preview1 = run_dir / "prepared-preview.png"
        bmp1 = run_dir / "prepared-bootlogo.bmp"

        prep1 = prepare_artwork(
            input1,
            bmp1,
            preview1,
            fit_mode=args.fit,
            background=args.background,
        )

        patch = build_patched_image(bmp1, custom)

        report = {
            "tool": f"KB1001 Splash Updater {VERSION}",
            "action": "update",
            "source_count": 1,
            "device_serial": args.serial,
            "slot_mapping": {
                "bootlogo.bmp": str(images[0]),
                "bootlogo-go.bmp": str(images[0]),
            },
            "image_preparation": prep1,
            "patch_verification": patch,
            "flash": "NOT YET PERFORMED",
        }

        print()
        print("PRE-FLASH VERIFICATION: PASS")
        print(f"  Source:          {images[0]}")
        print("  Mapping:         same image -> bootlogo.bmp + bootlogo-go.bmp")
        print(f"  Image fit:       {args.fit} (automatic full-screen default)")
        print(f"  Preview:         {preview1}")
        print(f"  Prepared BMP:    {bmp1}")

    else:
        # Two images: first -> bootlogo.bmp, second -> bootlogo-go.bmp.
        input1 = run_dir / ("input-bootlogo" + images[0].suffix.lower())
        input2 = run_dir / ("input-bootlogo-go" + images[1].suffix.lower())
        shutil.copy2(images[0], input1)
        shutil.copy2(images[1], input2)

        preview1 = run_dir / "prepared-preview-bootlogo.png"
        preview2 = run_dir / "prepared-preview-bootlogo-go.png"
        bmp1 = run_dir / "prepared-bootlogo.bmp"
        bmp2 = run_dir / "prepared-bootlogo-go.bmp"

        prep1 = prepare_artwork(
            input1,
            bmp1,
            preview1,
            fit_mode=args.fit,
            background=args.background,
        )
        prep2 = prepare_artwork(
            input2,
            bmp2,
            preview2,
            fit_mode=args.fit,
            background=args.background,
        )

        patch = build_patched_image(bmp1, custom, bmp2)

        report = {
            "tool": f"KB1001 Splash Updater {VERSION}",
            "action": "update",
            "source_count": 2,
            "device_serial": args.serial,
            "slot_mapping": {
                "bootlogo.bmp": str(images[0]),
                "bootlogo-go.bmp": str(images[1]),
            },
            "bootlogo_image_preparation": prep1,
            "bootlogo_go_image_preparation": prep2,
            "patch_verification": patch,
            "flash": "NOT YET PERFORMED",
        }

        print()
        print("PRE-FLASH VERIFICATION: PASS")
        print(f"  bootlogo.bmp:     {images[0]}")
        print(f"  bootlogo-go.bmp:  {images[1]}")
        print(f"  Image fit:        {args.fit} (automatic full-screen default)")
        print(f"  Preview #1:       {preview1}")
        print(f"  Preview #2:       {preview2}")

    report_path = write_report(run_dir, report)

    print(f"  Patched image:   {custom}")
    print(f"  Patched SHA256:  {patch['output_sha256']}")
    print(f"  FAT check:       {patch['fsck']}")
    print(f"  Report:          {report_path}")

    if args.no_flash:
        print("\n--no-flash specified; stopping after verified image creation.")
        return 0

    confirm_flash(
        f"This will flash {TARGET_PARTITION} on verified device {args.serial}.",
        args.yes,
    )

    flash = flash_image(args.serial, custom, patch["output_sha256"])
    report["flash"] = flash
    write_report(run_dir, report)

    print()
    print("UPDATE COMPLETE")
    print(f"  Source images:   {len(images)}")
    print(f"  Device:          {args.serial}")
    print(f"  Partition:       {TARGET_PARTITION}")
    print(f"  SHA256 verified: {flash['verified_sha256']}")
    print(f"  Run archive:     {run_dir}")
    return 0

def cmd_restore(args) -> int:
    verify_stock_base()
    verify_android_device(args.serial)
    confirm_flash(
        f"This will restore the exact stock {TARGET_PARTITION} on {args.serial}.",
        args.yes,
    )

    run_dir = create_run_dir()
    report = {
        "tool": f"KB1001 Splash Updater {VERSION}",
        "action": "restore",
        "device_serial": args.serial,
        "stock_image": str(STOCK_IMG),
        "stock_sha256": EXPECTED_STOCK_SHA256,
        "flash": "NOT YET PERFORMED",
    }
    write_report(run_dir, report)

    flash = flash_image(args.serial, STOCK_IMG, EXPECTED_STOCK_SHA256)
    report["flash"] = flash
    write_report(run_dir, report)

    print()
    print("RESTORE COMPLETE")
    print(f"  Device:          {args.serial}")
    print(f"  Stock SHA256:    {flash['verified_sha256']}")
    print(f"  Run archive:     {run_dir}")
    return 0


def cmd_prepare(args) -> int:
    verify_stock_base()
    run_dir = create_run_dir()
    input_copy = run_dir / ("input" + args.image.suffix.lower())
    shutil.copy2(args.image, input_copy)
    preview = run_dir / "prepared-preview.png"
    bmp = run_dir / "prepared-bootlogo.bmp"
    custom = run_dir / "bootloader_a-custom.img"

    prep = prepare_artwork(
        input_copy, bmp, preview, fit_mode=args.fit, background=args.background
    )
    patch = build_patched_image(bmp, custom)
    report = {
        "tool": f"KB1001 Splash Updater {VERSION}",
        "action": "prepare-only",
        "image_preparation": prep,
        "patch_verification": patch,
    }
    report_path = write_report(run_dir, report)

    print("PREPARE/VERIFY COMPLETE")
    print(f"  Preview:        {preview}")
    print(f"  Prepared BMP:   {bmp}")
    print(f"  Patched image:  {custom}")
    print(f"  SHA256:         {patch['output_sha256']}")
    print(f"  Report:         {report_path}")
    return 0


def cmd_status(args) -> int:
    print(f"KB1001 Splash Updater v{VERSION}")
    print(f"Host: {host_name()}")
    try:
        print(f"ADB: {resolve_host_tool('adb')}")
    except Exception as e:
        print(f"ADB: unavailable ({e})")
    try:
        print(f"Fastboot: {resolve_host_tool('fastboot')}")
    except Exception as e:
        print(f"Fastboot: unavailable ({e})")
    print()

    try:
        verify_stock_base()
        print("Local stock base: PASS")
        print(f"  {STOCK_IMG}")
        print(f"  SHA256 {EXPECTED_STOCK_SHA256}")
    except Exception as e:
        print(f"Local stock base: FAIL\n  {e}")

    print()
    try:
        info = verify_android_device(args.serial)
        print("Device: PASS")
        for k, v in info.items():
            print(f"  {k}: {v}")
        current = adb_shell(
            args.serial,
            f"su -c 'sha256sum /dev/block/by-name/{TARGET_PARTITION}'",
        )
        print(f"  current {TARGET_PARTITION}: {current}")
        if current.split()[0] == EXPECTED_STOCK_SHA256:
            print("  splash state: STOCK")
        else:
            print("  splash state: CUSTOM/UNKNOWN HASH")
    except Exception as e:
        print(f"Device: unavailable/failed verification\n  {e}")
    return 0


def interactive_menu(parser) -> int:
    print(f"KB1001 Splash Updater v{VERSION}")
    print("=" * 32)
    print("1) Update splash image")
    print("2) Restore stock splash")
    print("3) Status")
    print("0) Exit")
    print()
    choice = input("Choice: ").strip()

    if choice == "0":
        return 0
    if choice == "3":
        ns = parser.parse_args(["status"])
        return ns.func(ns)
    if choice == "2":
        ns = parser.parse_args(["restore"])
        return ns.func(ns)
    if choice == "1":
        raw1 = input("Image path: ").strip().strip('"').strip("'")
        if not raw1:
            raise SafetyError("No image path supplied.")
        raw2 = input("Second image path (Enter = use first for both): ").strip().strip('"').strip("'")
        argv = ["update", raw1]
        if raw2:
            argv.append(raw2)
        ns = parser.parse_args(argv)
        return ns.func(ns)
    raise SafetyError("Unknown menu choice.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Automatic verified KB1001 boot splash updater/restorer."
    )
    p.add_argument(
        "--serial",
        default=DEFAULT_SERIAL,
        help=f"Expected tablet serial (default {DEFAULT_SERIAL}).",
    )

    sub = p.add_subparsers(dest="command")

    def image_args(sp):
        sp.add_argument("image", type=Path, help="Input PNG/JPG/BMP/etc.")
        sp.add_argument(
            "--fit",
            choices=("contain", "cover", "stretch"),
            default="cover",
            help="cover=center crop/full bleed (default), contain=letterbox, stretch=distort",
        )
        sp.add_argument(
            "--background",
            default="black",
            help="Background for contain mode (default black).",
        )

    u = sub.add_parser(
        "update",
        help="Update from one image (both slots) or two images (one per slot).",
    )
    u.add_argument(
        "images",
        type=Path,
        nargs="+",
        help="One image for both slots, or two images: bootlogo.bmp then bootlogo-go.bmp.",
    )
    u.add_argument(
        "--fit",
        choices=("contain", "cover", "stretch"),
        default="cover",
        help="cover=center crop/full bleed (default), contain=letterbox, stretch=distort",
    )
    u.add_argument(
        "--background",
        default="black",
        help="Background for contain mode (default black).",
    )
    u.add_argument("--yes", action="store_true", help="Skip the FLASH confirmation.")
    u.add_argument("--no-flash", action="store_true", help="Stop after creating/verifying image.")
    u.set_defaults(func=cmd_update)

    r = sub.add_parser("restore", help="Restore exact stock bootloader_a.")
    r.add_argument("--yes", action="store_true", help="Skip the FLASH confirmation.")
    r.set_defaults(func=cmd_restore)

    pr = sub.add_parser("prepare", help="Prepare and verify without touching device.")
    image_args(pr)
    pr.set_defaults(func=cmd_prepare)

    st = sub.add_parser("status", help="Verify local base and connected tablet.")
    st.set_defaults(func=cmd_status)

    return p


def main() -> int:
    parser = build_parser()
    try:
        if len(sys.argv) == 1:
            return interactive_menu(parser)

        # Minimal file-input mode:
        #   python kb1001_splash_updater.py image.png
        #   python kb1001_splash_updater.py image1.png image2.png
        # Existing one/two file arguments are automatically an update.
        raw_args = sys.argv[1:]
        if 1 <= len(raw_args) <= 2 and all(Path(a).is_file() for a in raw_args):
            raw_args = ["update"] + raw_args

        args = parser.parse_args(raw_args)
        if not hasattr(args, "func"):
            parser.print_help()
            return 1
        return args.func(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except SafetyError as e:
        print(f"\nREFUSED: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())