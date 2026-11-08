# -*- coding: utf-8 -*-
"""從天堂R主程式的 .s32／.seg 地板圖塊畫地圖縮圖。只讀地圖與 Tile.pak，不複製地圖塊。"""
import os
import struct
import time

import re_tools

try:
    from PIL import Image
except ImportError:
    Image = None

DEFAULT_TILE = 140
DEFAULT_INDEX = 187
MAX_EDGE = 420
MIN_EDGE = 128


def rgb555(color):
    if color == 0:
        return None
    red = ((color >> 10) & 31) * 255 // 31
    green = ((color >> 5) & 31) * 255 // 31
    blue = (color & 31) * 255 // 31
    return red, green, blue


def average_block(block):
    """一顆 .til 區塊的不透明像素平均色。型別碼 bit 0x08 是留空，不畫。"""
    if not block or len(block) < 2:
        return None
    type_code = block[0]
    if type_code & 0x08:
        return None
    total_r = 0
    total_g = 0
    total_b = 0
    count = 0
    idx = 1
    length = len(block)

    def add_pixels(pixel_count):
        nonlocal idx, total_r, total_g, total_b, count
        for _ in range(pixel_count):
            if idx >= length - 1:
                return
            color = block[idx] | (block[idx + 1] << 8)
            idx += 2
            rgb = rgb555(color)
            if rgb is None:
                continue
            total_r += rgb[0]
            total_g += rgb[1]
            total_b += rgb[2]
            count += 1

    if (type_code & 0x02) == 0:
        for ty in range(24):
            if idx >= length - 1:
                break
            if ty <= 11:
                wide = (ty + 1) * 2
            else:
                wide = (23 - ty) * 2
            add_pixels(wide)
    else:
        if length < 5:
            return None
        idx += 3
        row_count = block[idx]
        idx += 1
        for _ty in range(row_count):
            if idx >= length:
                break
            seg_count = block[idx]
            idx += 1
            for _seg in range(seg_count):
                if idx + 1 >= length:
                    break
                idx += 1
                pixel_count = block[idx]
                idx += 1
                add_pixels(pixel_count)
    if count == 0:
        return None
    return (total_r // count, total_g // count, total_b // count)


class TileColors:
    """Tile.pak（R 版 _RMS）裡 .til 區塊的平均色。同一個區塊只算一次。"""

    def __init__(self, tile_idx):
        self.entries = {}
        for name, offset, size, packed, flags in re_tools.rpak_entries(tile_idx):
            self.entries[name.lower()] = (offset, size, packed, flags)
        self.pak = open(os.path.splitext(tile_idx)[0] + '.pak', 'rb')
        self.files = {}
        self.colors = {}

    def close(self):
        self.pak.close()

    def color(self, tile_id, index_id):
        key = (tile_id, index_id)
        if key in self.colors:
            return self.colors[key]
        blocks = self._file(tile_id)
        if blocks is None or index_id < 0 or index_id >= len(blocks):
            self.colors[key] = None
            return None
        value = average_block(blocks[index_id])
        self.colors[key] = value
        return value

    def _file(self, tile_id):
        if tile_id in self.files:
            return self.files[tile_id]
        ent = self.entries.get('%d.til' % tile_id)
        if ent is None:
            self.files[tile_id] = None
            return None
        offset, size, packed, flags = ent
        self.pak.seek(offset)
        raw = self.pak.read(packed if flags else size)
        if flags == 2:
            import brotli
            raw = brotli.decompress(raw)
        elif flags != 0:
            self.files[tile_id] = None
            return None
        if len(raw) < 8:
            self.files[tile_id] = None
            return None
        nblocks = struct.unpack_from('<i', raw, 0)[0]
        if nblocks <= 0 or nblocks > 100000:
            self.files[tile_id] = None
            return None
        need = 4 + (nblocks + 1) * 4
        if need > len(raw):
            self.files[tile_id] = None
            return None
        offsets = struct.unpack_from('<%di' % (nblocks + 1), raw, 4)
        base = need
        blocks = []
        for i in range(nblocks):
            start = base + offsets[i]
            end = base + offsets[i + 1]
            if start < 0 or end > len(raw) or end < start:
                blocks.append(b'')
            else:
                blocks.append(raw[start:end])
        self.files[tile_id] = blocks
        return blocks


def floor_cells(path):
    """地板 64×128 半格。(indexId, tileId)。0xFFFFFFFF 換成預設地磚，同 MapBlock。"""
    compact = path.lower().endswith('.seg')
    if compact:
        raw = open(path, 'rb').read(64 * 128 * 2)
        cells = []
        for i in range(0, len(raw) - 1, 2):
            packed = raw[i] | (raw[i + 1] << 8)
            cells.append((packed & 0xFF, packed >> 8))
        return cells
    data = open(path, 'rb').read(64 * 128 * 4)
    cells = []
    for i in range(0, len(data) - 3, 4):
        index_id = data[i]
        tile_id = data[i + 1] | (data[i + 2] << 8) | (data[i + 3] << 16)
        if index_id == 0xFF and tile_id == 0xFFFFFF:
            x = (i // 4) % 128
            index_id = DEFAULT_INDEX + (x & 1)
            tile_id = DEFAULT_TILE
        cells.append((index_id, tile_id))
    return cells


def object_cells(path):
    """物件群組裡的圖塊。座標是半格；0xCD,0xCD 是刪除格。"""
    if path.lower().endswith('.seg'):
        return []
    data = open(path, 'rb').read()
    p = 64 * 128 * 4
    if p + 2 > len(data):
        return []
    layer2 = struct.unpack_from('<H', data, p)[0]
    p += 2 + layer2 * 6 + 64 * 128 * 2
    if p + 4 > len(data):
        return []
    groups = struct.unpack_from('<i', data, p)[0]
    p += 4
    if groups < 0 or groups > 20000:
        return []
    found = []
    for _group in range(groups):
        if p + 4 > len(data):
            break
        count = struct.unpack_from('<H', data, p + 2)[0]
        p += 4
        if count < 0 or count > 20000:
            break
        for _i in range(count):
            if p + 7 > len(data):
                return found
            x = data[p]
            y = data[p + 1]
            index_id = data[p + 3]
            tile_id = data[p + 4] | (data[p + 5] << 8) | (data[p + 6] << 16)
            p += 7
            if x == 0xCD and y == 0xCD:
                continue
            found.append((x, y, index_id, tile_id))
    return found


def list_blocks(folder):
    blocks = []
    for fn in os.listdir(folder):
        low = fn.lower()
        if low.endswith('.s32.bro'):
            continue
        if not (low.endswith('.s32') or low.endswith('.seg')):
            continue
        stem = os.path.splitext(fn)[0]
        if len(stem) != 8:
            continue
        try:
            bx = int(stem[0:4], 16)
            by = int(stem[4:8], 16)
        except ValueError:
            continue
        blocks.append((bx, by, os.path.join(folder, fn)))
    return blocks


def forced_color(tiles, tile_id, index_id):
    """留空圖塊（型別碼 0x08）平常不畫；整張都是留空時改取它的顏色，避免這張地圖沒有圖。"""
    blocks = tiles._file(tile_id)
    if blocks is None or index_id < 0 or index_id >= len(blocks):
        return None
    raw = blocks[index_id]
    if not raw:
        return None
    patched = bytearray(raw)
    patched[0] = patched[0] & ~0x08
    return average_block(bytes(patched))


def blank_floor(blocks, tiles):
    rgb = None
    for _bx, _by, path in blocks:
        cells = floor_cells(path)
        if not cells:
            continue
        index_id, tile_id = cells[0]
        rgb = forced_color(tiles, tile_id, index_id)
        if rgb:
            break
    if rgb is None:
        rgb = (40, 44, 48)
    return Image.new('RGBA', (MIN_EDGE, MIN_EDGE), (rgb[0], rgb[1], rgb[2], 255))


def iso_canvas(span_x, span_y):
    """跟 MapCoordinates::mapWorldToPixel 一樣：像素 x＝2×(x+y)、y＝y−x。世界軸在畫面上是 45 度，菱形是 2:1。"""
    if span_x < 1 or span_y < 1:
        return 1, 1, 0
    width = 2 * (span_x + span_y - 2) + 2
    height = span_x + span_y - 1
    return width, height, span_x - 1


def put_iso(img, img_w, img_h, gx, gy, origin_y, rgb):
    sx = 2 * (gx + gy)
    sy = (gy - gx) + origin_y
    if sy < 0 or sy >= img_h:
        return 0
    written = 0
    for dx in (0, 1):
        px = sx + dx
        if px < 0 or px >= img_w:
            continue
        offset = (sy * img_w + px) * 4
        img[offset] = rgb[0]
        img[offset + 1] = rgb[1]
        img[offset + 2] = rgb[2]
        img[offset + 3] = 255
        written += 1
    return written


def paint_map(blocks, tiles):
    min_bx = min(b[0] for b in blocks)
    min_by = min(b[1] for b in blocks)
    max_bx = max(b[0] for b in blocks)
    max_by = max(b[1] for b in blocks)
    span_x = (max_bx - min_bx + 1) * 64
    span_y = (max_by - min_by + 1) * 64
    width, height, origin_y = iso_canvas(span_x, span_y)
    img = bytearray(width * height * 4)
    opaque = 0
    for bx, by, path in blocks:
        cells = floor_cells(path)
        ox = (bx - min_bx) * 64
        oy = (by - min_by) * 64
        for y in range(64):
            row = y * 128
            for lx in range(64):
                colors = []
                for half in (0, 1):
                    i = row + lx * 2 + half
                    if i >= len(cells):
                        continue
                    index_id, tile_id = cells[i]
                    rgb = tiles.color(tile_id, index_id)
                    if rgb:
                        colors.append(rgb)
                if not colors:
                    continue
                red = 0
                green = 0
                blue = 0
                for rgb in colors:
                    red += rgb[0]
                    green += rgb[1]
                    blue += rgb[2]
                n = len(colors)
                opaque += put_iso(img, width, height, ox + lx, oy + y, origin_y,
                                  (red // n, green // n, blue // n))
        for x, y, index_id, tile_id in object_cells(path):
            if x >= 128 or y >= 64:
                continue
            rgb = tiles.color(tile_id, index_id)
            if not rgb:
                continue
            opaque += put_iso(img, width, height, ox + (x // 2), oy + y, origin_y, rgb)
    if opaque == 0:
        return blank_floor(blocks, tiles)
    image = Image.frombytes('RGBA', (width, height), bytes(img))
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    resampling = getattr(Image, 'Resampling', Image)
    edge = max(image.size)
    if edge > MAX_EDGE:
        image.thumbnail((MAX_EDGE, MAX_EDGE), resampling.BOX)
    elif edge < MIN_EDGE and edge > 0:
        scale = MIN_EDGE / float(edge)
        image = image.resize(
            (max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale))),
            resampling.NEAREST)
    return image


def render(map_root, dest_dir, rows):
    """為 scan 出來的每一張地圖寫 R-<編號>.png。已經有的檔不重畫。回傳新畫的張數。"""
    if Image is None:
        raise RuntimeError('畫地圖縮圖需要 Pillow')
    os.makedirs(dest_dir, exist_ok=True)
    tile_idx = os.path.join(os.path.dirname(map_root), 'Tile.idx')
    if not os.path.isfile(tile_idx):
        raise RuntimeError('找不到 %s' % tile_idx)
    pending = []
    ready = 0
    for row in rows:
        name = 'R-%d.png' % row['id']
        dest = os.path.join(dest_dir, name)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            row['file'] = name
            ready += 1
            continue
        pending.append(row)
    print('地圖縮圖：全部 %d，已有 %d，要畫 %d' % (len(rows), ready, len(pending)))
    if not pending:
        return 0
    tiles = TileColors(tile_idx)
    drawn = 0
    failed = 0
    started = time.time()
    try:
        for i, row in enumerate(pending):
            folder = os.path.join(map_root, str(row['id']))
            blocks = list_blocks(folder)
            image = paint_map(blocks, tiles) if blocks else None
            name = 'R-%d.png' % row['id']
            if image is None:
                failed += 1
                print('地圖 %d 沒有可畫的地板' % row['id'])
                continue
            image.save(os.path.join(dest_dir, name))
            row['file'] = name
            drawn += 1
            if drawn == 1 or drawn % 25 == 0 or i + 1 == len(pending):
                print('地圖縮圖 %d／%d（地圖 %d，%.0f 秒）' % (
                    drawn, len(pending), row['id'], time.time() - started))
    finally:
        tiles.close()
    print('地圖縮圖寫入 %d 張，畫不出來 %d 張' % (drawn, failed))
    return drawn
