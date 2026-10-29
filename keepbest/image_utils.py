"""Image loading, hashing and pixel-level comparison. Depends only on Pillow."""
import hashlib
import importlib
import io
import math
import re
import signal
import threading

from PIL import Image, ImageChops, ImageFilter, ImageOps

try:  # Colour-profile conversion (bundled with the official Pillow wheels)
    from PIL import ImageCms
    _SRGB = ImageCms.createProfile("sRGB")
except Exception:  # pragma: no cover - Pillow built without littlecms
    ImageCms = None

# Optional JPEG XL support. Pillow cannot read JXL on its own; without this
# plugin JXL files are still handled by Stage 1 but skipped (and reported) in Stage 3.
try:
    importlib.import_module("pillow_jxl")  # registers the JXL format with Pillow
    JXL_SUPPORTED = True
except ImportError:
    JXL_SUPPORTED = False

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.jxl',
                    '.bmp', '.tif', '.tiff'}

# Worst allowed average difference (0-255) inside any 8x8 cell of the comparison
# image. Measured on test data: re-encodes/resizes score <= 15, small real edits
# (text, watermark, patch, recolour, shifted subject) score >= 28.
STRICTNESS_PRESETS = {"strict": 12, "normal": 20, "loose": 28}

_CELL = 8


# "photo (1).jpg", "photo (2) (1).jpg": the copy numbers browsers and file
# managers add. "(0)" and other brackets like "(edit)" are left alone.
_COPY_NUMBER_RE = re.compile(r"\s*\([1-9]\d*\)$")


def has_copy_number(path) -> bool:
    return bool(_COPY_NUMBER_RE.search(path.stem))


def strip_copy_number(path):
    """Return *path* with trailing copy numbers removed from the name, or None
    when nothing (or nothing sensible) would remain."""
    stem = path.stem
    while True:
        cleaned = _COPY_NUMBER_RE.sub("", stem)
        if cleaned == stem:
            break
        stem = cleaned
    if stem == path.stem or not stem.strip():
        return None
    return path.with_name(stem + path.suffix)


def copy_base(path) -> str:
    """Name without copy numbers, case-folded: 'Photo (2).JPG' -> 'photo'."""
    stripped = strip_copy_number(path)
    return (stripped or path).stem.casefold()


def rename_target(keep, group_paths):
    """New name for a kept copy, or None.

    Only when another file of the same group shares the base name, which shows
    the number is a copy number ("photo.jpg" + "photo (1).jpg") and not part of
    a series ("Holiday (12).jpg" next to "IMG_5.jpg" is left alone).
    """
    target = strip_copy_number(keep)
    if target is None:
        return None
    base = copy_base(keep)
    if any(copy_base(p) == base for p in group_paths if p != keep):
        return target
    return None


def ignore_sigint():
    """Worker-process initializer: let only the main process handle Ctrl+C."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def sha256_file(path, chunk_size=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


# Bit depths that greyscale pictures with more than 8 bits are usually stored in.
_DEPTHS = (8, 10, 12, 14, 16)
# Greyscale with more than 8 bits (integer or float); compared pairwise, see pixel_difference.
_HIGH_BIT_MODES = ("I", "I;16", "I;16B", "I;16L", "I;16N", "F")


def _to_8_bit(img):
    """16/32-bit integer and float greyscale to 8 bit, scaled to the bit depth the
    values actually use.

    A plain convert() clips everything above 255 to white, which makes unrelated
    16-bit pictures look identical. Dividing by 256 instead squeezes 10- or 12-bit
    data (stored in a 16-bit file) into the darkest few levels, where an edit
    scores far below the limit. So the values are scaled from the smallest usual
    depth that holds them; float pictures from 0-1 when they stay in that range."""
    if img.mode == "F":
        low, high = img.getextrema()
        _check_finite(low, high)
        if high <= 1.0:
            return img.point(lambda v: v * 255).convert("L")
    else:
        img = img.convert("I")
        _, high = img.getextrema()
    high = max(0, int(high))
    bits = next((b for b in _DEPTHS if high < 1 << b), high.bit_length())
    return img.point(lambda v: v * (256 / (1 << bits))).convert("L")


def _check_finite(*values):
    if not all(math.isfinite(v) for v in values):
        raise ValueError("pixel values are not finite (NaN or infinity), can't be compared")


# Formats whose extra frames are an animation. In others they are further pictures
# of their own: an MPO's second camera or depth map, a TIFF's other pages.
_ANIMATION_FORMATS = {"GIF", "PNG", "WEBP", "AVIF", "JXL"}


def _normalize(img, max_side):
    """Return an RGB image, orientation-corrected, colour-managed, with transparency
    flattened, downscaled so its longest side is at most `max_side`."""
    if img.format in ("JPEG", "MPO"):
        img.draft("RGB", (max_side, max_side))  # fast DCT-domain downscaling
    icc = img.info.get("icc_profile")
    img = ImageOps.exif_transpose(img)

    if img.mode in _HIGH_BIT_MODES:
        img = _to_8_bit(img)

    img.thumbnail((max_side, max_side), Image.LANCZOS, reducing_gap=3.0)

    # Flatten transparency onto white; otherwise hidden RGB values under transparent
    # pixels (often black) decide the comparison.
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        img = Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255, 255)), rgba)
        img = img.convert("RGB")

    # Convert to sRGB so the same photo exported with different profiles still matches.
    if icc and ImageCms is not None:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if img.mode not in ("RGB", "L", "CMYK"):
                img = img.convert("RGB")
            return ImageCms.profileToProfile(img, src, _SRGB, outputMode="RGB")
        except Exception:
            pass
    return img.convert("RGB")


def _dhash(gray, size):
    """Horizontal + vertical difference hash as one int of 2*size*size bits."""
    bits = 0
    h = gray.resize((size + 1, size), Image.LANCZOS).tobytes()
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | (h[base + col] > h[base + col + 1])
    v = gray.resize((size, size + 1), Image.LANCZOS).tobytes()
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (v[row * size + col] > v[(row + 1) * size + col])
    return bits


def _webp_is_lossless(path):
    """Walk the RIFF chunks: 'VP8L' = lossless bitstream, 'VP8 ' = lossy."""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WEBP":
            return False
        while chunk := f.read(8):
            if len(chunk) < 8:
                return False
            fourcc, size = chunk[:4], int.from_bytes(chunk[4:], "little")
            if fourcc == b"VP8L":
                return True
            if fourcc == b"VP8 ":
                return False
            f.seek(size + (size & 1), 1)
    return False


# TIFF compressions that lose detail; all others (none, LZW, Deflate, PackBits,
# CCITT, ...) keep every pixel. Pillow reports them in img.info["compression"].
_LOSSY_TIFF = {"jpeg", "tiff_jpeg", "webp", "tiff_sgilog", "tiff_sgilog24"}


def is_lossless(path, fmt, compression=None):
    """Best-effort guess whether the file stores pixels without lossy compression:
    True, False, or None when it can't be told.

    JPEG XL can be either, and Pillow can't tell which, so it is None ("unknown").
    TIFF is judged by its *compression*."""
    if fmt in ("PNG", "BMP"):
        return True
    if fmt == "TIFF":
        return compression not in _LOSSY_TIFF
    if fmt == "JXL":
        return None
    if fmt == "WEBP":
        try:
            return _webp_is_lossless(path)
        except OSError:
            return False
    return False  # JPEG, AVIF, GIF (palette-reduced) and unknown formats


def analyze_image(path, hash_size, format_ranks, prefer_jxl=False):
    """Collect metadata and perceptual hash in one decode. Runs in worker processes.
    Returns a dict; on failure the dict contains an 'error' key.

    *prefer_jxl* (config "prefer_jxl"): rank JPEG XL files like lossless ones, so
    a JXL copy is kept instead of a lossless original (PNG, TIFF, ...).

    Animations are only reported (not hashed). Other files with several frames
    (MPO photos from cameras and phones, multi-page TIFFs) are compared by their
    first picture; 'frames' tells find_groups to never remove them."""
    try:
        stat = path.stat()
        with Image.open(path) as img:
            fmt = (img.format or path.suffix[1:]).upper()
            frames = getattr(img, "n_frames", 1)
            if frames > 1 and fmt in _ANIMATION_FORMATS:
                return {'path': path, 'animated': True}
            width, height = img.size
            orientation = img.getexif().get(0x0112, 1)
            compression = img.info.get("compression")
            if orientation in (5, 6, 7, 8):  # displayed rotated by 90 degrees
                width, height = height, width
            norm = _normalize(img, 256)
        lossless = is_lossless(path, fmt, compression)
        resolution = width * height
        # An MPO is a JPEG with more pictures appended; ".jpg" without a format is JPEG.
        rank_as = {"MPO": "JPEG", "JPG": "JPEG", "TIF": "TIFF"}.get(fmt, fmt) if fmt not in format_ranks else fmt
        return {
            'path': path,
            'width': width,
            'height': height,
            'resolution': resolution,
            'format': fmt,
            'format_rank': format_ranks.get(rank_as, -1),
            'lossless': lossless,
            'lossless_rank': lossless is True or (fmt == "JXL" and prefer_jxl),
            'bpp': (stat.st_size * 8) / resolution if resolution else 0.0,
            'age': stat.st_mtime,
            'size': stat.st_size,
            'mtime_ns': stat.st_mtime_ns,
            'numbered': has_copy_number(path),
            'animated': False,
            'frames': frames,
            'hash': _dhash(norm.convert("L"), hash_size),
        }
    except Exception as e:
        return {'path': path, 'error': f"{type(e).__name__}: {e}"}


def _square_side(width, height, max_side):
    return max(64, min(max_side, width, height)) // _CELL * _CELL


def comparison_thumbnail(path, max_side):
    """Square thumbnail used for pixel verification: (side, RGB bytes, None), or for
    greyscale with more than 8 bits (side, None, (float bytes, brightest value)),
    with the values as stored, on a 16-bit scale. Never upscales beyond the image's
    own short side."""
    with Image.open(path) as img:
        if img.mode in _HIGH_BIT_MODES:
            return _high_bit_thumbnail(img, max_side)
        norm = _normalize(img, max_side * 2)
    side = _square_side(norm.width, norm.height, max_side)
    thumb = norm.resize((side, side), Image.LANCZOS, reducing_gap=3.0)
    return side, thumb.tobytes(), None


def _high_bit_thumbnail(img, max_side):
    """Keep the stored values: how bright they are meant to be (10, 12 or 16 bits
    used?) can only be told by comparing with the other picture of a pair."""
    img = ImageOps.exif_transpose(img)
    grey = img if img.mode == "F" else img.convert("F")
    low, high = grey.getextrema()
    _check_finite(low, high)
    if img.mode == "F" and high <= 1.0:  # floats from 0 to 1
        grey, high = grey.point(lambda v: v * 65535), high * 65535
    grey.thumbnail((max_side * 2, max_side * 2), Image.LANCZOS, reducing_gap=3.0)
    side = _square_side(grey.width, grey.height, max_side)
    grey = grey.resize((side, side), Image.LANCZOS)
    return side, None, (grey.tobytes(), max(high, 1.0))


def _peak(thumb):
    """The brightest value of a thumbnail on a 16-bit scale (8-bit values x 257)."""
    side, rgb, high = thumb
    if high is not None:
        return high[1]
    return max(band[1] for band in Image.frombytes("RGB", (side, side), rgb).getextrema()) * 257


def _as_rgb(thumb, scale):
    """The thumbnail as an 8-bit RGB image, its 16-bit-scale values times *scale*."""
    side, rgb, high = thumb
    if high is not None:
        grey = Image.frombytes("F", (side, side), high[0]).point(lambda v: v * scale).convert("L")
        return Image.merge("RGB", (grey, grey, grey))
    bands = Image.frombytes("RGB", (side, side), rgb).split()
    return Image.merge("RGB", [b.convert("F").point(lambda v: v * 257 * scale).convert("L") for b in bands])


def pixel_difference(thumb_a, thumb_b):
    """Worst 8x8-cell mean difference (0-255) between two comparison thumbnails.
    See _cells for how they are compared."""
    return _cells(thumb_a, thumb_b).getextrema()[1]


def difference_spot(thumb_a, thumb_b, blurred=None):
    """pixel_difference and where it is: (score, (left, top, size)), the worst
    cell as fractions of the picture. Comparison thumbnails are the whole upright
    picture squeezed into a square, so the fractions hold for the picture itself.
    *blurred*: a Blurred to take the blurred pictures from."""
    cells = _cells(thumb_a, thumb_b, blurred)
    data = cells.tobytes()
    worst = max(data)
    index = data.index(worst)
    grid = cells.width
    return worst, (round(index % grid / grid, 5), round(index // grid / grid, 5), round(1 / grid, 5))


def _cells(thumb_a, thumb_b, blurred=None):
    """The mean difference of each 8x8 cell (an "L" image, one pixel per cell).
    The images are compared at the smaller of the two resolutions, lightly blurred
    to absorb resampling/compression noise; the per-cell maximum (not a global mean)
    is what catches small local edits such as added text.

    When one of them is greyscale with more than 8 bits, both are brought to one
    scale first and stretched together so the brighter one's brightest value is
    white: then a picture and a copy of it twice as bright never look alike, a dark
    16-bit picture still matches its 8-bit copy, and edits in 10- or 12-bit data
    stored in 16-bit files are not squeezed into a few dark levels."""
    (sa, _, ha), (sb, _, hb) = thumb_a, thumb_b
    side = min(sa, sb)
    if ha is None and hb is None:
        a, b = (blurred.get(t) if blurred is not None and t[0] == side else _blur(_rgb(t), side)
                for t in (thumb_a, thumb_b))
    else:
        scale = 255 / max(_peak(thumb_a), _peak(thumb_b), 1.0)
        a, b = _blur(_as_rgb(thumb_a, scale), side), _blur(_as_rgb(thumb_b, scale), side)
    diff = ImageChops.difference(a, b)
    r, g, bl = diff.split()
    worst_channel = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    return worst_channel.resize((side // _CELL, side // _CELL), Image.BOX)


def preview_jpeg(path, max_side, quality=85):
    """The picture as the comparison sees it (upright, colour-managed, transparency
    on white, greyscale with more than 8 bits scaled), as a JPEG for a browser:
    browsers can't show every format keepbest reads (JPEG XL, TIFF, 16 bit, CMYK)."""
    with Image.open(path) as img:
        norm = _normalize(img, max_side)
    out = io.BytesIO()
    norm.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def aspect_ratio_close(a, b, tolerance):
    if min(a['width'], a['height'], b['width'], b['height']) <= 0:
        return False
    ra, rb = a['width'] / a['height'], b['width'] / b['height']
    return abs(ra - rb) / max(ra, rb) <= tolerance


def score_image(meta):
    """Higher is better: resolution, then lossless over lossy or unknown (a lossy
    re-encode must never beat its lossless master; JXL counts as lossless here
    with "prefer_jxl"), then format preference, then a name without
    a copy number ("photo.jpg" beats "photo (1).jpg"), then bits-per-pixel (less
    compressed, meaningful within one format), then oldest file."""
    lossless = meta.get('lossless_rank')
    if lossless is None:  # an older session (or an image saved again from one)
        lossless = meta['lossless'] is True
    return (meta['resolution'], lossless, meta['format_rank'],
            not meta['numbered'],
            meta['bpp'], -meta['age'])


# The parts of score_image in its order, named: the page says why a file is kept.
RANK_CRITERIA = ("resolution", "lossless", "format", "name", "bpp", "age")


def deciding_criterion(keeper: dict, other: dict) -> str | None:
    """The first part of score_image in which *keeper* ranks above *other*, or
    None when it doesn't (a file chosen by hand, the same score, or a saved
    session without the fields)."""
    try:
        for name, a, b in zip(RANK_CRITERIA, score_image(keeper), score_image(other)):
            if a != b:
                return name if a > b else None
    except (KeyError, TypeError):
        return None
    return None


def _rgb(thumb):
    side, rgb, _ = thumb
    return Image.frombytes("RGB", (side, side), rgb)


def _blur(image, side):
    """The picture at *side*, lightly blurred, as the comparison uses it."""
    if image.width != side:
        image = image.resize((side, side), Image.LANCZOS)
    return image.filter(ImageFilter.GaussianBlur(1))


class Blurred:
    """The blurred pictures of up to *size* 8-bit thumbnails, as compared at their
    own size. Blurring is most of a comparison's work, and a picture is compared
    with several others in turn. Threads may share one."""

    def __init__(self, size: int = 32):
        self.size, self._kept, self._lock = size, {}, threading.Lock()

    def get(self, thumb):
        with self._lock:
            kept = self._kept.get(id(thumb))
        if kept is not None:
            return kept[1]
        image = _blur(_rgb(thumb), thumb[0])
        with self._lock:
            if len(self._kept) < self.size:
                self._kept.setdefault(id(thumb), (thumb, image))   # the thumb keeps its id taken
        return image

    def forget(self, thumb) -> None:
        with self._lock:
            self._kept.pop(id(thumb), None)
