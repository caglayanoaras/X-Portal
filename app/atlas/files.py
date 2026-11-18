"""Uploads for image and file attributes, kept in atlas.db (table atlas_file).

An object's image or file column holds the id of an atlas_file row. Each stored
file belongs to one object attribute: replacing or clearing it, or deleting the
object, deletes the file, and schema changes sweep away files nothing refers to.

Images must be PNG, JPEG, GIF, WebP, TIFF or BMP; Pillow opens each one to check
it. Every image gets a small JPEG thumbnail for grids, and a JPEG preview when a
browser can't show the original well (TIFF, or more than PREVIEW_SIZE pixels on
a side); downloads always give the original. Files must be PDF, Office or
OpenDocument (or CSV), checked by their extension and their first bytes.
"""
import hashlib
import io
import os
import re
import unicodedata
import warnings
from urllib.parse import quote

from PIL import Image, ImageOps

from app.atlas.errors import AtlasError

IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "MPO": "image/jpeg", "GIF": "image/gif",
                 "WEBP": "image/webp", "TIFF": "image/tiff", "BMP": "image/bmp"}
BROWSER_FORMATS = {"PNG", "JPEG", "MPO", "GIF", "WEBP", "BMP"}       # browsers show these as they are
IMAGE_ACCEPT = ".png,.jpg,.jpeg,.gif,.webp,.tif,.tiff,.bmp"

_ZIP = (b"PK\x03\x04",)
_OLE = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)
# extension -> (MIME type, the bytes such a file starts with; None for plain text)
DOCUMENTS = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".doc": ("application/msword", _OLE),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", _ZIP),
    ".xls": ("application/vnd.ms-excel", _OLE),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", _ZIP),
    ".xlsm": ("application/vnd.ms-excel.sheet.macroEnabled.12", _ZIP),
    ".ppt": ("application/vnd.ms-powerpoint", _OLE),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", _ZIP),
    ".odt": ("application/vnd.oasis.opendocument.text", _ZIP),
    ".ods": ("application/vnd.oasis.opendocument.spreadsheet", _ZIP),
    ".odp": ("application/vnd.oasis.opendocument.presentation", _ZIP),
    ".rtf": ("application/rtf", (b"{\\rtf",)),
    ".csv": ("text/csv", None),
}
DOCUMENT_ACCEPT = ",".join(DOCUMENTS)
INLINE_TYPES = {"application/pdf"}       # besides images, opened in the browser instead of saved

THUMB_SIZE = 240
PREVIEW_SIZE = 2400
VIEWS = ("original", "preview", "thumb")


def clean_name(filename) -> str:
    """The file name without folders or control characters, at most 200 characters."""
    name = re.split(r"[\\/]", str(filename or ""))[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:200] or "file"


def prepare(kind: str, filename, data: bytes) -> dict:
    """Check an upload for an image or file attribute; what insert() stores."""
    name = clean_name(filename)
    if not data:
        raise AtlasError(400, f"'{name}' is empty.")
    out = {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "data": data,
           "width": None, "height": None, "preview": None, "thumb": None}
    out.update(_image(name, data) if kind == "image" else {"type": _document_type(name, data)})
    return out


def _image(name: str, data: bytes) -> dict:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            image = Image.open(io.BytesIO(data))
            image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AtlasError(400, f"'{name}' has too many pixels.") from None
    except Exception:  # Pillow raises many kinds of errors for files it can't read
        raise AtlasError(400, f"'{name}' isn't an image Atlas can read (PNG, JPEG, GIF, WebP, TIFF or BMP).") from None
    if image.format not in IMAGE_FORMATS:
        raise AtlasError(400, f"'{name}' is a {image.format} image; use PNG, JPEG, GIF, WebP, TIFF or BMP.")
    width, height = image.size
    shown = _displayable(ImageOps.exif_transpose(image))
    thumb = shown.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
    preview = None
    if image.format not in BROWSER_FORMATS or max(width, height) > PREVIEW_SIZE:
        big = shown.copy()
        big.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
        preview = _jpeg(big, 88)
    return {"type": IMAGE_FORMATS[image.format], "width": width, "height": height,
            "thumb": _jpeg(thumb, 82), "preview": preview}


def _displayable(image: Image.Image) -> Image.Image:
    """An RGB copy: 16-bit and float pictures (microscopes) stretched to 8 bits, transparency on white."""
    if image.mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        import numpy as np
        values = np.asarray(image, dtype=np.float32)
        low, high = float(values.min()), float(values.max())
        values = (values - low) * (255.0 / (high - low)) if high > low else np.zeros_like(values)
        return Image.fromarray(values.astype("uint8"), "L").convert("RGB")
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.getchannel("A"))
        return flat
    return image.convert("RGB")


def _jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _document_type(name: str, data: bytes) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext not in DOCUMENTS:
        raise AtlasError(400, f"'{name}' isn't a PDF or Office file. Allowed: {', '.join(DOCUMENTS)}.")
    mime, starts = DOCUMENTS[ext]
    if starts is None:
        if b"\x00" in data[:65536]:
            raise AtlasError(400, f"'{name}' isn't a text file.")
    elif not data.startswith(starts):
        raise AtlasError(400, f"'{name}' doesn't look like a {ext[1:].upper()} file.")
    return mime


def insert(conn, stored: dict, user: str, now: str) -> int:
    return conn.execute(
        "INSERT INTO atlas_file (name, type, size, width, height, sha256, data, preview, thumb, created_at,"
        " created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (stored["name"], stored["type"], stored["size"], stored["width"], stored["height"], stored["sha256"],
         stored["data"], stored["preview"], stored["thumb"], now, user)).lastrowid


def delete(conn, ids) -> None:
    ids = [i for i in ids if i is not None]
    if ids:
        conn.execute(f"DELETE FROM atlas_file WHERE id IN ({', '.join('?' * len(ids))})", ids)


def load(conn, file_id: int, view: str) -> dict:
    """One stored file as {name, type, sha256, body}; view is original, preview or thumb."""
    if view not in VIEWS:
        raise AtlasError(400, "view must be original, preview or thumb.")
    body = {"original": "data", "preview": "COALESCE(preview, data)", "thumb": "thumb"}[view]
    row = conn.execute(f"SELECT name, type, sha256, preview IS NOT NULL AS converted, {body} AS body "
                       f"FROM atlas_file WHERE id = ?", (file_id,)).fetchone()
    if row is None or row["body"] is None:
        raise AtlasError(404, "There is no such file." if row is None else "Only images have thumbnails.")
    media = row["type"]
    if view == "thumb" or (view == "preview" and row["converted"]):
        media = "image/jpeg"
    return {"name": row["name"], "type": media, "sha256": row["sha256"], "body": bytes(row["body"])}


_TURKISH = str.maketrans("İıŞşĞğÜüÖöÇç", "IiSsGgUuOoCc")


def content_disposition(name: str, inline: bool) -> str:
    """inline or attachment, with the name for new browsers (UTF-8) and an ASCII
    stand-in for old ones ("Ölçümler.xlsx" -> "Olcumler.xlsx")."""
    plain = unicodedata.normalize("NFKD", name.translate(_TURKISH)).encode("ascii", "ignore").decode()
    plain = plain.replace('"', "").replace("\\", "") or "file"
    return f"{'inline' if inline else 'attachment'}; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"
