"""Immutable local question figures, independent of OCR and source-file lifetime."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
import struct

IMAGE_URL = re.compile(r"!\[[^\]\n]*\]\((/question-images/[a-f0-9]{64}\.png)\)")


def _placeable_wmf(data):
    """Add the missing container header to Word's standard WMF pictures.

    Pillow accepts placeable WMF, while legacy equation previews often contain
    only METAHEADER. Bounds come from the original viewport records.
    """
    if not data.startswith(b"\x01\x00\x09\x00"):
        return data
    origin, extent, offset = (0, 0), None, 18
    while offset + 6 <= len(data):
        size, function = struct.unpack_from("<IH", data, offset)
        end = offset + size * 2
        if size < 3 or end > len(data):
            raise ValueError("Invalid WMF record")
        if function in {0x020B, 0x020C} and size >= 5:
            y, x = struct.unpack_from("<hh", data, offset + 6)
            if function == 0x020B:
                origin = (x, y)
            else:
                extent = (x, y)
        offset = end
    if extent is None or min(extent) <= 0:
        raise ValueError("WMF has no readable viewport")
    x, y = origin
    bounds = (x, y, x + extent[0], y + extent[1])
    if any(value < -32768 or value > 32767 for value in bounds):
        raise ValueError("WMF bounds exceed the placeable header")
    header = struct.pack("<IHhhhhHI", 0x9AC6CDD7, 0, *bounds, 1440, 0)
    checksum = 0
    for word in struct.unpack("<10H", header):
        checksum ^= word
    return header + struct.pack("<H", checksum) + data


def save_question_image(image, directory: Path):
    from PIL import Image, ImageOps

    opened = Image.open(BytesIO(_placeable_wmf(image))) if isinstance(image, bytes) else image
    try:
        if isinstance(image, bytes) and opened.format == "WMF":
            # Rasterize small formula previews at a readable resolution.
            opened.load(dpi=288)
        with ImageOps.exif_transpose(opened) as oriented:
            rgba = oriented.convert("RGBA")
            try:
                # Preserve the full diagram on a white background, including vector
                # pictures decoded by Pillow's Windows WMF/EMF handler.
                background = Image.new("RGBA", rgba.size, "white")
                try:
                    background.alpha_composite(rgba)
                    with background.convert("RGB") as rgb:
                        if min(rgb.size) <= 4 or all(high - low <= 8 for low, high in rgb.getextrema()):
                            return None
                        buffer = BytesIO()
                        rgb.save(buffer, format="PNG")
                finally:
                    background.close()
            finally:
                rgba.close()
        data = buffer.getvalue()
        name = sha256(data).hexdigest() + ".png"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        if not target.is_file():
            target.write_bytes(data)
        return "/question-images/" + name
    finally:
        if isinstance(image, bytes):
            opened.close()


def preserve_question_images(text, sources, *, append_missing=True):
    """Accept only figures present in the selected original source fragments."""
    allowed = {}
    for source in sources:
        for match in IMAGE_URL.finditer(source["text"]):
            allowed.setdefault(match[1], match[0])
    text = IMAGE_URL.sub(lambda match: match[0] if match[1] in allowed else "", text)
    present = {match[1] for match in IMAGE_URL.finditer(text)}
    missing = [markdown for url, markdown in allowed.items() if url not in present] if append_missing else []
    return text.strip() + ("\n\n" + "\n\n".join(missing) if missing else "")
