# .blend header and embedded preview spec

How the backend reads a `.blend` file in pure Python (stdlib only, no Blender):

1. **Header**: the version of Blender that saved the file, its pointer size and its endianness.
2. **Embedded preview**: the thumbnail Blender stores in the file (`TEST` block), written out as a PNG.

Everything below was checked against Blender 5.2.2 LTS (the only runnable Blender on the dev machine), the Blender source (`main` branch, links inline), and real files saved by Blender 2.90 to 5.2 found on this machine.

## Part 1: Header

### Output

```python
{"version": "4.2", "pointer_size": 8, "endian": "little",
 "compressed": None | "zstd" | "gzip", "header_format": "legacy" | "v1"}
```

The function returns `None` for anything it does not recognize: an empty file, a truncated file, wrong magic, a compressed stream that fails to decode or decodes to something that is not a `.blend`, or an unknown header size. It never raises on file content.

There is one exception. If the file has zstd magic and **no zstd decoder is available** (Python < 3.14, where the stdlib has no `compression.zstd` module), the function returns a partial dict: `compressed: "zstd"` and every other field `None`. This happens only when the decoder is missing, never because a stream is corrupt. Without a decoder, a corrupt zstd file can't be told apart from a valid one, so it also gets the partial dict.

### Step 1: read a bounded prefix

Read at most **256 KiB** (`f.read(262144)`). Never seek, and never trust lengths stored in the file. A zstd decoder emits output only after it has decoded a whole block, and a block is at most 128 KiB compressed, so 256 KiB is always enough to get the first bytes of a compressed file. Uncompressed files need only 17 bytes.

### Step 2: sniff compression by magic

| Bytes at offset 0 | Meaning | Written by |
|---|---|---|
| `42 4C 45 4E 44 45 52` (`BLENDER`) | uncompressed | all versions |
| `28 B5 2F FD` | zstd frame | Blender 3.0+ with "Compress" on |
| `1F 8B` | gzip | Blender < 3.0 with "Compress" on (newer versions can still read it) |
| anything else | not a .blend, return `None` | |

For a compressed file, decompress **at most 64 output bytes** from the prefix, then go to Step 3 with those bytes:

- **gzip**: `zlib.decompressobj(wbits=31).decompress(prefix, 64)` (stdlib on every Python version).
- **zstd, Python 3.14+**: `compression.zstd.ZstdDecompressor().decompress(prefix, max_length=64)` (stdlib).
- **zstd, Python < 3.14**: `from compression import zstd` raises `ImportError`. Return the partial dict described under Output. We don't add the `zstandard` package because it isn't in the tech stack.

Any other exception from the decompressor means a corrupt stream, so return `None`. Blender writes zstd as a series of frames, each holding 1 MiB of uncompressed data (`ZSTD_CHUNK_SIZE` in [writefile.cc](https://projects.blender.org/blender/blender/src/branch/main/source/blender/blenloader/intern/writefile.cc)), and ends the file with a seek-table skippable frame (observed: the file ends in `B1 EA 92 8F`). The header is always in the first frame, so a normal streaming decoder handles it.

### Step 3: parse the header

There are two formats. Try **v1 first**. A v1 header never matches the legacy pattern (byte 7 is a digit, not `_`/`-`), and a legacy header never matches the v1 pattern.

#### Legacy format: 12 bytes (Blender 2.x to 4.x; the newest observed is 4.4)

| Offset | Len | Value |
|---|---|---|
| 0 | 7 | `BLENDER` |
| 7 | 1 | pointer size: `_` = 4, `-` = 8 |
| 8 | 1 | endianness: `v` = little, `V` = big |
| 9 | 3 | ASCII decimal version, `major*100 + minor` (e.g. `402`) |

Regex: `rb"BLENDER([_-])([vV])(\d{3})"`, anchored at offset 0 (`re.match`).

#### v1 format: 17 bytes (Blender 5.0+)

| Offset | Len | Value |
|---|---|---|
| 0 | 7 | `BLENDER` |
| 7 | 2 | ASCII decimal header size, always `17` |
| 9 | 1 | `-` (always; v1 files are 64-bit only, so pointer_size = 8) |
| 10 | 2 | ASCII file-format version, `01` |
| 12 | 1 | endianness: `v` = little, `V` = big |
| 13 | 4 | ASCII decimal version, zero-padded, `major*100 + minor` (e.g. `0502`) |

Regex: `rb"BLENDER(\d\d)-(\d\d)([vV])(\d{4})"`. If the header size is not `17`, return `None`, because a future layout may move the fields. For the header, any file-format version is accepted as long as the size is 17. The preview reader is stricter (see Part 2).

#### Version to display string

Both formats use the same conversion: `v = int(digits)`, then `f"{v // 100}.{v % 100}"`. For example, `402` → `"4.2"`, `0502` → `"5.2"`, `290` → `"2.90"`, `306` → `"3.6"`. The version has no patch number, so 4.2.3 and 4.2.9 both write `402`.

### Reference implementation (header)

Tested on Python 3.14.3 (all paths) and 3.13.7 (the zstd fallback) against the vectors below and the real files listed in Part 3.

```python
import re
import zlib

MAX_READ = 256 * 1024
LEGACY = re.compile(rb"BLENDER([_-])([vV])(\d{3})")
V1 = re.compile(rb"BLENDER(\d\d)-(\d\d)([vV])(\d{4})")


def _inflate(data: bytes, kind: str) -> bytes:
    if kind == "gzip":
        return zlib.decompressobj(wbits=31).decompress(data, 64)
    from compression import zstd
    return zstd.ZstdDecompressor().decompress(data, max_length=64)


def parse_blend_header(path: str) -> dict | None:
    with open(path, "rb") as f:
        head = f.read(MAX_READ)
    compressed = None
    if head[:4] == b"\x28\xb5\x2f\xfd":
        compressed = "zstd"
    elif head[:2] == b"\x1f\x8b":
        compressed = "gzip"
    if compressed:
        try:
            head = _inflate(head, compressed)
        except ImportError:
            return {"version": None, "pointer_size": None, "endian": None, "compressed": "zstd", "header_format": None}
        except Exception:  # noqa: BLE001
            return None
    if m := V1.match(head):
        if m[1] != b"17":
            return None
        fmt, ptr, endian, ver = "v1", 8, m[3], int(m[4])
    elif m := LEGACY.match(head):
        fmt, ptr, endian, ver = "legacy", 8 if m[1] == b"-" else 4, m[2], int(m[3])
    else:
        return None
    return {
        "version": f"{ver // 100}.{ver % 100}",
        "pointer_size": ptr,
        "endian": "little" if endian == b"v" else "big",
        "compressed": compressed,
        "header_format": fmt,
    }
```

`ImportError` can only come from the zstd branch, because gzip uses `zlib`, which is always available. A truncated zstd stream such as `28 B5 2F FD 00` decodes to `b""` without raising, then fails the regex and returns `None` (on 3.14+).

### Test vectors (observed)

These are the first 32 bytes, after decompression, of real files on this machine (Windows x64). The parser only needs the bytes up to the header length. The bytes after that are the first BHead (`GLOB`/`REND`).

| Saved by | File | Header (ASCII) | First 32 bytes (hex) | Expected |
|---|---|---|---|---|
| 2.90 | add-on library | `BLENDER-v290` | `42 4C 45 4E 44 45 52 2D 76 32 39 30 52 45 4E 44 48 00 00 00 80 C1 DF B9 23 00 00 00 00 00 00 00` | `2.90`, 8, little, legacy |
| 3.6 | userpref.blend | `BLENDER-v306` | `42 4C 45 4E 44 45 52 2D 76 33 30 36 47 4C 4F 42 50 04 00 00 60 CD FF 58 BD 00 00 00 9B 00 00 00` | `3.6`, 8, little, legacy |
| 4.1 | userpref.blend | `BLENDER-v401` | `42 4C 45 4E 44 45 52 2D 76 34 30 31 47 4C 4F 42 50 04 00 00 20 D7 BF 6D 3C 00 00 00 A4 00 00 00` | `4.1`, 8, little, legacy |
| 4.2 | userpref.blend | `BLENDER-v402` | `42 4C 45 4E 44 45 52 2D 76 34 30 32 47 4C 4F 42 50 04 00 00 F0 D4 DF 4E 7C 00 00 00 AB 00 00 00` | `4.2`, 8, little, legacy |
| 4.3 | userpref.blend | `BLENDER-v403` | `42 4C 45 4E 44 45 52 2D 76 34 30 33 47 4C 4F 42 50 04 00 00 D0 CE 3F 07 34 00 00 00 AB 00 00 00` | `4.3`, 8, little, legacy |
| 4.4 | userpref.blend | `BLENDER-v404` | `42 4C 45 4E 44 45 52 2D 76 34 30 34 47 4C 4F 42 50 04 00 00 90 D7 7F F5 AD 00 00 00 AA 00 00 00` | `4.4`, 8, little, legacy |
| 5.0 | userpref.blend | `BLENDER17-01v0500` | `42 4C 45 4E 44 45 52 31 37 2D 30 31 76 30 35 30 30 47 4C 4F 42 AA 00 00 00 10 00 00 00 00 00 00` | `5.0`, 8, little, v1 |
| 5.2 | scene, plain and zstd | `BLENDER17-01v0502` | `42 4C 45 4E 44 45 52 31 37 2D 30 31 76 30 35 30 32 52 45 4E 44 00 00 00 00 10 00 00 00 00 00 00` | `5.2`, 8, little, v1 (`compressed` = None / `"zstd"`) |
| 3.0 | zstd, written by Blender | `BLENDER-v300` | raw `28 B5 2F FD A0 F4 04 01 …`; decoded `42 4C 45 4E 44 45 52 2D 76 33 30 30 52 45 4E 44 48 00 00 00 …` | `3.0`, 8, little, legacy, zstd |
| 4.4 | zstd, written by Blender | `BLENDER-v404` | raw `28 B5 2F FD 60 F4 8B 25 …`; decoded `42 4C 45 4E 44 45 52 2D 76 34 30 34 52 45 4E 44 48 00 00 00 …` | `4.4`, 8, little, legacy, zstd |

Negative vectors that must return `None`: an empty file; `NOTBLENDNOTBLEND…`; the truncated `BLENDER-v4`; `BLENDER99-01v0500…` (unknown v1 size); a gzip stream with corrupt data (`1F 8B 08` + random bytes); and, on Python 3.14+ only, the truncated zstd `28 B5 2F FD 00`, a zstd stream with corrupt data (`28 B5 2F FD` + random bytes), and a valid zstd stream of non-blend bytes. On Python < 3.14 every input with zstd magic returns the partial dict.

## Part 2: Embedded preview

Blender writes a preview image into most `.blend` files, in a block with the code `TEST`. The backend extracts it without starting Blender and writes it to the cache as a PNG. If there is no usable preview, it falls back to a Blender thumbnail-only job (`blender_export_proxy.py` without `--out-glb`).

### File layout (decompressed stream)

```
header (12 bytes legacy | 17 bytes v1)
BHead "REND" + RenderInfo data          one block observed in every file
BHead "TEST" + preview data              only if a preview was saved
BHead "GLOB" + FileGlobal ...            then all other blocks ... "ENDB"
```

The order is fixed by `write_file_handle` in [writefile.cc](https://projects.blender.org/blender/blender/src/branch/main/source/blender/blenloader/intern/writefile.cc): `write_renderinfo` (REND), then `write_thumb` (TEST, only when a thumbnail exists), then `write_global` (GLOB). Blender's own standalone extractor ([blendthumb_extract.cc](https://projects.blender.org/blender/blender/src/branch/main/source/blender/blendthumb/src/blendthumb_extract.cc)) uses the same walk: it skips `REND` blocks, returns at `TEST`, and treats any other code as "no thumbnail". We do the same, so files without a preview stop at `GLOB` after reading about 300 bytes.

Observed in every real file (Part 3): exactly one `REND` (72 bytes of data in legacy files, 264 in v1 files; a 5.2 file with three scenes still has one), followed directly by `TEST` or by `GLOB`. In 5.2 `cam.blend`, `TEST` is at offset 313 = 17 + 32 + 264. Preferences files (`userpref.blend`) start with `GLOB`, so they have no preview.

### BHead layouts

A BHead is the header in front of every block. All fields are in the file's byte order, and the struct has no padding. Source: [BLO_core_bhead.hh](https://projects.blender.org/blender/blender/src/branch/main/source/blender/blenloader_core/BLO_core_bhead.hh). Selection logic: `BlenderHeader::bhead_type()` in `blo_core_blend_header.cc`.

| Name | Used when | Fields in order | Size | Python `struct` (little-endian) | `len` index |
|---|---|---|---|---|---|
| `BHead4` | legacy, pointer `_` (4) | `code char[4]`, `len int32`, `old uint32`, `SDNAnr int32`, `nr int32` | 20 | `"<4siIii"` | 1 |
| `SmallBHead8` | legacy, pointer `-` (8) | `code char[4]`, `len int32`, `old uint64`, `SDNAnr int32`, `nr int32` | 24 | `"<4siQii"` | 1 |
| `LargeBHead8` | v1 (`BLENDER17-01…`) | `code char[4]`, `SDNAnr int32`, `old uint64`, `len int64`, `nr int64` | 32 | `"<4siQqq"` | 3 |

`len` is the number of data bytes that follow the BHead. The next BHead starts at `bhead_offset + size + len`. The `code` is four ASCII bytes. Two-letter ID codes are NUL-padded (`WM\0\0`).

Real bytes, 5.2 (v1): `52 45 4E 44 | 00 00 00 00 | 10 00 00 00 00 00 00 00 | 08 01 00 00 00 00 00 00 | 01 00 00 00 00 00 00 00` = REND, SDNAnr 0, old 0x10, len 264, nr 1.
Real bytes, 4.4 (legacy 8): `52 45 4E 44 | 48 00 00 00 | 90 CF 9F 4F 7C 00 00 00 | 00 00 00 00 | 01 00 00 00` = REND, len 72, old, SDNAnr 0, nr 1.

### TEST block payload

Defined by `BlendThumbnail` in [BKE_main.hh](https://projects.blender.org/blender/blender/src/branch/main/source/blender/blenkernel/BKE_main.hh):

| Offset in data | Type | Value |
|---|---|---|
| 0 | int32 | width |
| 4 | int32 | height |
| 8 | `uint8[width*height*4]` | pixels, RGBA8 |

- **Size**: `len` must equal `8 + width*height*4` exactly. Blender's extractor rejects anything else, and we do too.
- **Row order**: **bottom-up** (ImBuf origin is bottom-left). Blender's extractor calls `thumb_data_vertical_flip` before writing a PNG. Confirmed empirically: a GUI save with Suzanne in the upper-left of the camera view comes out upper-left after the flip, and the default cube and the user's `House.blend` come out upright.
- **Alpha**: **premultiplied**. Camera previews are drawn with `R_ALPHAPREMUL` (`blend_file_thumb_from_camera` in [wm_files.cc](https://projects.blender.org/blender/blender/src/branch/main/source/blender/windowmanager/intern/wm_files.cc)). Confirmed empirically: on the edges of the grey default cube, semi-transparent pixels look like `(16,16,16,45)` and `(60,61,62,127)`, so RGB scales with alpha. In the red Suzanne GUI preview, `(24,2,2,52)` appears, and across 422 semi-transparent pixels from two files no channel exceeds alpha. PNG stores straight alpha, so **un-premultiply**: `c = min(255, (c*255 + a//2) // a)` when `0 < a < 255`. Screenshot previews are fully opaque (alpha 255 everywhere), so un-premultiplying them changes nothing.
- **Dimensions**: Blender always writes at most 128 pixels in each direction (`BLEN_THUMB_SIZE 128`). Camera previews are 128x128. Screenshot previews are 128 wide, with height set by the window aspect ratio (observed 128x67 to 128x78).
- **Endianness**: width and height are in the file's byte order. We support little-endian only. Blender's extractor returns `BT_INVALID_FILE` for big-endian files, and Blender 5.0+ refuses to open them.

### Preview types (`Preferences > Save & Load > File Preview Type`)

API: [`PreferencesFilePaths.file_preview_type`](https://docs.blender.org/api/current/bpy.types.PreferencesFilePaths.html#bpy.types.PreferencesFilePaths.file_preview_type), values `NONE`, `AUTO` (default), `SCREENSHOT`, `CAMERA`. Observed on 5.2.2:

| Saved with | GUI session | Background (`blender -b`) |
|---|---|---|
| `NONE` | no `TEST` block | no `TEST` block |
| `CAMERA` | 128x128 offscreen solid-mode draw from `scene.camera`, transparent background, premultiplied | **no new render**; see below |
| `SCREENSHOT` | screenshot of the main window, 128xN, opaque | **no new render** |
| `AUTO` | `CAMERA` if the scene has a camera and the screen has a 3D viewport, otherwise `SCREENSHOT` (no-camera file → 128x68 screenshot) | **no new render** |

In background mode both `blend_file_thumb_from_camera` and `..._from_screenshot` return `nullptr` (`if (G.background ...)`). For any type other than `NONE`, the saved file then gets `bmain->blen_thumb`, which is the preview that was loaded with the current file. Results:

- `blender -b --factory-startup` + build a scene + save → the `TEST` block holds the **factory startup's default-cube image**, whatever the scene contains. Verified: cube, dense Suzanne, a no-camera scene and an empty scene saved with AUTO, CAMERA or SCREENSHOT all have the same pixels (md5 `76c079cd…`). Only `NONE` leaves out `TEST`.
- `blender -b in.blend` + save → the preview from `in.blend` is carried over unchanged, or there is none if `in.blend` had none. Verified both cases.

So an embedded preview can be stale: it shows the scene as it was at the last interactive save, and pipeline-generated files may carry a generic default-cube image. That is fine for an image-only preview, but QA fixtures generated with `blender -b` all share the default-cube preview, so tests can't use the preview to tell those fixtures apart.

### Algorithm

1. Open the file and sniff the magic (same table as Part 1).
   - zstd → `compression.zstd.ZstdFile(raw)`. It is streaming and handles Blender's multiple frames and the trailing skippable seek-table frame. On Python < 3.14 the import fails → `None` (use the Blender fallback).
   - gzip → `gzip.GzipFile(fileobj=raw)`.
   - otherwise read the raw file.
2. Read 12 bytes. If bytes 7..8 are `17`, read 5 more. Then match the header:
   - v1: require size `17`, file-format `01` and `v`. Pick `LargeBHead8`. A different file-format version may change the BHead, so return `None`.
   - legacy: require `v` and version ≥ 250 (Blender's extractor returns `BT_EARLY_VERSION` below 2.50). Pick `SmallBHead8` for `-` or `BHead4` for `_`.
   - otherwise return `None`.
3. Loop: read one BHead (a short read means `None`). Add `size + len` to a running total. If `len < 0` or the total is greater than **`MAX_BYTES` = 2 MiB**, return `None`. Read `len` bytes (a short read means `None`). On `REND`, continue the loop. On `TEST`, go to step 4. On any other code, return `None`.
4. Check `len ≥ 8`, read width and height as `<ii`, require `1 ≤ w, h ≤ 512` and `len == 8 + w*h*4`, otherwise `None`.
5. Flip the rows to top-down and un-premultiply. Return `(w, h, rgba)`.
6. Any exception (I/O, `zstd.ZstdError`, `gzip.BadGzipFile`, `EOFError`, `zlib.error`, `struct.error`) → `None`. Never raise.

**Bounds.** `MAX_BYTES` caps the decompressed bytes consumed, and no length from the file is used before it is checked against that cap. 2 MiB was chosen instead of 1 MiB because the largest allowed preview (512x512) has a 1,048,584-byte payload, which together with the header and `REND` is just over 1 MiB. In practice Blender's ≤128x128 preview means about 66 KB is read (17 + 32 + 264 + 32 + 65,544) and decoding stops there. A real `.blend` stops right after the preview, or at `GLOB` when there is none.

### Reference implementation (preview)

Tested on Python 3.14.3 and ruff-clean. It is self-contained; the `LEGACY`/`V1` regexes are the same as in Part 1 and can be shared.

```python
import gzip
import re
import struct
import zlib

MAX_BYTES = 2 << 20
MAX_DIM = 512
LEGACY = re.compile(rb"BLENDER([_-])([vV])(\d{3})")
V1 = re.compile(rb"BLENDER(\d\d)-(\d\d)([vV])(\d{4})")
V1_BHEAD = struct.Struct("<4siQqq")
SMALL_BHEAD8 = struct.Struct("<4siQii")
BHEAD4 = struct.Struct("<4siIii")


def _scan(f) -> tuple[int, int, bytes] | None:
    head = f.read(12)
    if head[7:9] == b"17":
        head += f.read(5)
    if m := V1.fullmatch(head):
        if m[1] != b"17" or m[2] != b"01" or m[3] != b"v":
            return None
        bhead, len_field = V1_BHEAD, 3
    elif m := LEGACY.fullmatch(head):
        if m[2] != b"v" or int(m[3]) < 250:
            return None
        bhead, len_field = (SMALL_BHEAD8 if m[1] == b"-" else BHEAD4), 1
    else:
        return None
    used = len(head)
    while True:
        raw = f.read(bhead.size)
        if len(raw) != bhead.size:
            return None
        fields = bhead.unpack(raw)
        code, length = fields[0], fields[len_field]
        used += bhead.size + length
        if length < 0 or used > MAX_BYTES:
            return None
        data = f.read(length)
        if len(data) != length:
            return None
        if code == b"TEST":
            break
        if code != b"REND":
            return None
    if length < 8:
        return None
    w, h = struct.unpack_from("<ii", data)
    if not (1 <= w <= MAX_DIM and 1 <= h <= MAX_DIM) or length != 8 + w * h * 4:
        return None
    stride = w * 4
    px = bytearray().join(data[8 + y * stride : 8 + (y + 1) * stride] for y in reversed(range(h)))
    for i in range(3, len(px), 4):
        a = px[i]
        if 0 < a < 255:
            for c in range(i - 3, i):
                px[c] = min(255, (px[c] * 255 + a // 2) // a)
    return w, h, bytes(px)


def read_embedded_preview(path: str) -> tuple[int, int, bytes] | None:
    try:
        with open(path, "rb") as raw:
            magic = raw.read(4)
            raw.seek(0)
            if magic == b"\x28\xb5\x2f\xfd":
                from compression import zstd
                return _scan(zstd.ZstdFile(raw))
            if magic[:2] == b"\x1f\x8b":
                return _scan(gzip.GzipFile(fileobj=raw))
            return _scan(raw)
    except Exception:  # noqa: BLE001
        return None


def rgba_to_png(w: int, h: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    stride = w * 4
    scanlines = b"".join(b"\x00" + rgba[y * stride : (y + 1) * stride] for y in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(scanlines))
        + chunk(b"IEND", b"")
    )
```

`rgba_to_png` writes an 8-bit RGBA PNG with straight alpha, a single IDAT chunk and filter 0 on every row. Write the bytes to a temp file and `os.replace` them into `.cache/<asset_id>.png`, as with any other cache file.

### Test vectors (preview)

Synthetic vectors that QA can build with `struct` (all were checked against the reference implementation):

- **Valid**: a header followed by two `REND` blocks (264 zero bytes each), then `TEST` with payload `<ii 2 2>` + bottom row `FF0000FF 00FF00FF` + top row `0000FFFF 40404080`, then `GLOB`. The expected result is `(2, 2, 0000FFFF 80808080 FF0000FF 00FF00FF)`: flipped, and `(64,64,64,128)` un-premultiplied to `(128,128,128,128)`. The same result must come from v1 (`<4siQqq`), legacy 8 (`BLENDER-v404`, `<4siQii`), legacy 4 (`BLENDER_v404`, `<4siIii`), the gzip-wrapped version, and the zstd version split across two frames in the middle of the `TEST` block.
- **`None`**: big-endian `BLENDER-V404`; version `249`; v1 file-format `02`; no `TEST` (REND, GLOB); `GLOB` before `TEST`; width 0; width 513; payload one byte too long; payload of 4 bytes; file cut off inside the `TEST` data; `len` of -8; a `REND` with `len` 2^40; header only; empty file; random bytes; zstd magic + garbage; gzip magic + garbage; valid zstd of non-blend bytes; a missing path.

Real-file expectations, which the fixture generator can reproduce with Blender 5.2: a file saved by `blender -b --factory-startup` with the default `AUTO` preference gives 128x128 (the default-cube image), and the same save with `file_preview_type = "NONE"` gives `None`.

## Part 3: Verification record

### Embedded preview on real files (read-only)

All files are 64-bit little-endian. Extraction took 0.2 to 0.5 ms per file (zstd 0.3 to 1.1 ms, plus about 3 ms on the first call to import `compression.zstd`). PNGs were checked visually for orientation on these files: 3.6 `House.blend`, 4.3 `twobirds.blend`, 4.4 zstd `grass_medium_01_2k.blend`, the 5.2 default cube, and the 5.2 GUI-saved Suzanne.

| Saved by | Format | Compression | Blocks seen | Preview |
|---|---|---|---|---|
| 2.90 | legacy `-v` | none | REND 72, TEST, GLOB | 128x128 |
| 3.0 | legacy | none / **zstd** | REND 72, TEST, GLOB | 128x75 / 128x128 |
| 3.4, 3.5 | legacy | none | REND 72, TEST, GLOB | 128x67, 128x128 |
| 3.6 | legacy | none | REND 72, TEST, GLOB | 128x128 (5 files) |
| 4.2 | legacy | none | REND 72, TEST, GLOB | 128x128 (2 files) |
| 4.3 | legacy | none / **zstd** | REND 72, TEST, GLOB | 128x128 / 128x70, 128x77 |
| 4.4 | legacy | **zstd** | REND 72, TEST, GLOB | 128x68 (screenshot) |
| 3.6 to 5.0 | `userpref.blend` | none | GLOB, USER, … | `None` (no preview) |
| 5.0 | v1 | zstd | REND 264, TEST, GLOB (one file: REND, GLOB) | 128x128, 128x70, 128x72; `None` for the Storyboarding template |
| 5.1 | v1 | zstd | REND 264, GLOB | `None` (VFX template, saved without preview) |
| 5.2 | v1 | none / zstd | REND 264, TEST, GLOB | 128x128, 128x67 to 128x78 |

## Limitations

- **4-byte pointers (`BHead4`) and big-endian files**: neither was observed. `BHead4` is implemented from `BLO_core_bhead.hh` and tested only with synthetic files. Big-endian files are rejected (`None`).
- **gzip (pre-3.0 "Compress")**: no real gzip-compressed `.blend` was found on this machine. The gzip path was tested only on gzip-wrapped copies of real and synthetic headers.
- **Blender 4.5 LTS** was not observed. It may write legacy or v1; both readers handle either.
- **Python < 3.14**: with no zstd decoder, `read_embedded_preview` returns `None` for zstd files, and the header parser returns the partial dict. Both fall back gracefully, but compressed files then always need the Blender thumbnail job.
- **Staleness**: the preview is whatever Blender wrote at the last interactive save. See Preview types.
