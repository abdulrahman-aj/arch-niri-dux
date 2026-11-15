"""PNG validation and pixel decoding shared by host and guest checks."""

import struct
import zlib


def png_ready(path):
    # Niri writes asynchronously; existence alone can expose a partial screenshot.
    return path.is_file() and path.read_bytes().endswith(b'\x00\x00\x00\x00IEND\xaeB\x60\x82')


def png_dimensions(path):
    data = path.read_bytes()
    assert data[:8] == b'\x89PNG\r\n\x1a\n', 'Not a PNG screenshot.'
    assert data[12:16] == b'IHDR', 'Screenshot PNG has no IHDR.'
    width, height = struct.unpack('>II', data[16:24])
    assert width > 0 and height > 0, 'Rendered screenshot has no dimensions.'
    return width, height


def png_top_rows(path, count):
    """Decode the top rows of an 8-bit RGB(A) PNG into (r, g, b) tuples per pixel.

    The host has no imaging library; screenshots are non-interlaced truecolor.
    """
    data = path.read_bytes()
    width, _ = png_dimensions(path)
    depth, color, interlace = data[24], data[25], data[28]
    assert depth == 8 and color in (2, 6) and interlace == 0, 'Unsupported screenshot PNG format.'
    channels = 4 if color == 6 else 3
    compressed, offset = b'', 8
    while offset < len(data):
        (length,) = struct.unpack('>I', data[offset : offset + 4])
        if data[offset + 4 : offset + 8] == b'IDAT':
            compressed += data[offset + 8 : offset + 8 + length]
        offset += 12 + length
    stride = width * channels
    raw = zlib.decompressobj().decompress(compressed, (stride + 1) * count)
    rows, previous = [], bytearray(stride)
    for index in range(count):
        kind = raw[index * (stride + 1)]
        line = bytearray(raw[index * (stride + 1) + 1 : (index + 1) * (stride + 1)])
        for x in range(stride):
            left = line[x - channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            if kind == 1:
                line[x] = (line[x] + left) & 0xFF
            elif kind == 2:
                line[x] = (line[x] + up) & 0xFF
            elif kind == 3:
                line[x] = (line[x] + (left + up) // 2) & 0xFF
            elif kind == 4:
                estimate = left + up - corner
                nearest = min(
                    (abs(estimate - left), 0, left), (abs(estimate - up), 1, up), (abs(estimate - corner), 2, corner)
                )
                line[x] = (line[x] + nearest[2]) & 0xFF
        rows.append([tuple(line[x : x + 3]) for x in range(0, stride, channels)])
        previous = line
    return rows
