"""File-type detection by magic bytes, falling back to the extension.

Extension-based detection is a trap in exactly this domain: a scanner that emits
``Scan_20240412.pdf`` containing JPEG bytes is common, and so is a ``.jpg`` that is
really a PNG. ``filetype`` sniffs the header; it only knows binary formats, so text
formats still fall through to the suffix.
"""

from __future__ import annotations

from pathlib import Path

import filetype

from osdc.domain.enums import FileType

_MIME_TO_TYPE: dict[str, FileType] = {
    "application/pdf": FileType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": FileType.DOCX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": FileType.PPTX,
}

_SUFFIX_TO_TYPE: dict[str, FileType] = {
    ".pdf": FileType.PDF,
    ".docx": FileType.DOCX,
    ".pptx": FileType.PPTX,
    ".txt": FileType.TXT,
    ".md": FileType.MD,
    ".markdown": FileType.MD,
    ".rst": FileType.TXT,
    ".log": FileType.TXT,
}

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".heic"}


def detect_type(path: Path) -> FileType:
    kind = filetype.guess(str(path))
    if kind is not None:
        mime = kind.mime
        if mime in _MIME_TO_TYPE:
            return _MIME_TO_TYPE[mime]
        if mime.startswith("image/"):
            return FileType.IMAGE

    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return FileType.IMAGE
    return _SUFFIX_TO_TYPE.get(suffix, FileType.OTHER)
