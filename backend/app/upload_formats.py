"""Accepted uploads shared by the API and document parser."""

TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"})
TIKA_SUFFIXES = frozenset({".doc", ".rtf", ".ppt"})
SUPPORTED_SUFFIXES = frozenset({".pdf", ".pptx", ".docx"}) | TEXT_SUFFIXES | IMAGE_SUFFIXES | TIKA_SUFFIXES
SUPPORTED_FORMATS_LABEL = "PDF、Word（DOC/DOCX/RTF）、PPT（PPT/PPTX）、TXT、Markdown、图片（PNG/JPG/JPEG/WEBP/BMP/TIF/TIFF）"
