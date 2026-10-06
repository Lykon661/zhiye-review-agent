"""Recover simple MathType equations from their original WMF character positions.

No OCR or equation-specific substitutions: glyph order, scripts and operators
come from WMF records. Unsupported drawing structures keep their image fallback.
Record layout: Microsoft MS-WMF sections 2.3.3.5 and 2.1.2.3.
"""
import re
import struct


def wmf_formula(data: bytes) -> str | None:
    if not data.startswith(b"\x01\x00\x09\x00") or b"MathType" not in data:
        return None
    try:
        return _read_formula(data)
    except (ValueError, struct.error, UnicodeError):
        return None


def _read_formula(data):
    fonts, selected, glyphs = [], None, []
    x, y, alignment, offset = 0, 0, 0, 18
    ignored = {0x0102, 0x0103, 0x0201, 0x0209, 0x020B, 0x020C, 0x0626}
    ended = False
    while offset + 6 <= len(data):
        size, function = struct.unpack_from("<IH", data, offset)
        end = offset + size * 2
        if size < 3 or end > len(data):
            raise ValueError("Invalid WMF record")
        record = data[offset + 6:end]
        if function == 0:
            ended = True
            break
        if function == 0x02FB:  # META_CREATEFONTINDIRECT
            height, width, rotation, orientation, weight = struct.unpack_from("<5h", record)
            if height >= 0 or rotation or orientation:
                selected_font = None
            else:
                face = record[18:].split(b"\0", 1)[0].decode("ascii")
                selected_font = (abs(height), face, record[13])
            try:
                index = fonts.index(None)
                fonts[index] = selected_font
            except ValueError:
                fonts.append(selected_font)
        elif function == 0x012D:  # META_SELECTOBJECT
            index = struct.unpack_from("<H", record)[0]
            selected = fonts[index] if index < len(fonts) else None
        elif function == 0x01F0:  # META_DELETEOBJECT
            index = struct.unpack_from("<H", record)[0]
            if index < len(fonts):
                fonts[index] = None
        elif function == 0x012E:  # META_SETTEXTALIGN
            alignment = struct.unpack_from("<H", record)[0]
        elif function == 0x0214:  # META_MOVETO
            y, x = struct.unpack_from("<hh", record)
        elif function == 0x0A32:  # META_EXTTEXTOUT
            py, px, count, flags = struct.unpack_from("<hhHH", record)
            if selected is None or not count or flags & ~6 or alignment & 0x1E != 0x18:
                raise ValueError("Unsupported formula text layout")
            start = 8 + (8 if flags & 6 else 0)
            advances = start + count + count % 2
            if advances + count * 2 > len(record):
                raise ValueError("Character advances are missing")
            text = record[start:start + count].decode("ascii")
            height, face, charset = selected
            if charset not in {0, 1, 2}:
                raise ValueError("Unsupported equation character set")
            # Symbol encodes many letters differently. Only these signs share
            # their ASCII meaning, so don't guess the rest of that font's glyphs.
            if (charset == 2 or face.casefold() == "symbol") and re.search(r"[^=+\-*/().,<> ]", text):
                raise ValueError("Unsupported Symbol glyph")
            if re.search(r"[^A-Za-z0-9=+\-*/().,<> ]", text):
                raise ValueError("Unsupported equation glyph")
            dx = struct.unpack_from("<" + "h" * count, record, advances)
            if not alignment & 1:
                x, y = px, py
            for character, advance in zip(text, dx):
                if advance < 0:
                    raise ValueError("Right-to-left formula")
                if not character.isspace():
                    glyphs.append((x, y, height, character))
                x += advance
        elif function not in ignored:
            # Fractions, radicals and matrices can contain additional strokes.
            # Refuse these rather than losing a semantic piece of the equation.
            raise ValueError("Equation contains unsupported drawing records")
        offset = end
    if not ended or not glyphs:
        raise ValueError("Incomplete equation")

    size = max(glyph[2] for glyph in glyphs)
    main = sorted((glyph for glyph in glyphs if glyph[2] >= size * 0.85), key=lambda glyph: glyph[0])
    baseline = main[0][1]
    if any(abs(glyph[1] - baseline) > size * 0.08 for glyph in main):
        raise ValueError("Equation has more than one main baseline")
    scripts = {}
    for px, py, height, character in glyphs:
        if height >= size * 0.85:
            continue
        if abs(py - baseline) < size * 0.15 or not character.isalnum():
            raise ValueError("Uncertain script position")
        preceding = [index for index, glyph in enumerate(main) if glyph[0] <= px]
        if not preceding:
            raise ValueError("Script has no base")
        index = preceding[-1]
        if not main[index][3].isalnum() and main[index][3] != ")":
            raise ValueError("Invalid script base")
        kind = "_" if py > baseline else "^"
        scripts.setdefault((index, kind), []).append((px, character))
    parts = []
    for index, glyph in enumerate(main):
        parts.append(glyph[3])
        for kind in ("_", "^"):
            values = scripts.get((index, kind))
            if values:
                parts.append(kind + "{" + "".join(character for _, character in sorted(values)) + "}")
    latex = "".join(parts)
    # A written 'mod' is an operator; do not alter its adjacent operands.
    return re.sub(r"mod(?=[0-9(])", lambda _: r"\bmod ", latex)
