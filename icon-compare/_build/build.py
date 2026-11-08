# -*- coding: utf-8 -*-
r"""381 → R 版道具、技能圖示對照網頁的建置腳本（2026-11-04）。

2026-11-06 從 GameLauncher\artifacts\icon-compare 搬到 GameAssets 專案。

重跑：在 GameAssets 根目錄執行
    python icon-compare/_build/build.py              產生 icon-compare/data.js、img/381/*.png、img/r/*.png（圖已存在就略過）
    python icon-compare/_build/build.py --out <資料夾> 輸出到別的資料夾（例如驗證能否從零重建）
    python icon-compare/_build/build.py --check       只檢查 data.js 引用的圖是否都在、img 裡有沒有沒被引用的圖

來源路徑（全部唯讀，可用環境變數改）：
    GA_GAMELAUNCHER  GameLauncher 專案（預設 D:\程式碼測試區\C++\GameLauncher；用它的 tools/re/re_tools.py）
    GA_CLIENT        381 客戶端資源（預設 <GA_GAMELAUNCHER>\client）
    GA_BACKEND       381 Java 後端（預設 C:\Users\eric7\eclipse-workspace\Lineage381C；用 db\*.sql、src\）
    GA_RCLIENT       天堂R 客戶端（預設 D:\天堂資料\天堂-主程式懶人包\天堂R\LineageRemastered）

資料來源：
- 381 道具：後端 db/etcitem.sql（etcitem，2026-09-23 匯出）、db/renewal_381.sql（weapon、armor，2026-08-18 匯出）。
- 381 技能：db/renewal_381.sql 的 skills；圖示＝client/spellicon.idx[skill_id-1]（同 GameLauncher src/App.cpp App::skillIcon）。
- 381 圖：Sprite.pak、Sprite00～15.pak 的 <圖號>.tbt（同 App::cachedIcon），用 GameAssets tools/export-icon-pngs.cpp
  編出的 _build/export-icon-pngs.exe 轉 png（編法見 README.md）。
- R：LineageRemastered/data.idx 的 contents/item-common.bin（protobuf：欄位 1＝道具編號、2＝圖示編號、
  4＝未鑑定名稱、5＝鑑定後名稱，名稱是 $N＝desc-c.tbl 第 N+1 行）、SpellList-c.xml（主動技能）、
  passiveSpells-c.xml（被動技能）；圖＝icon.idx 的 <圖示編號>.png。解包用 GameLauncher tools/re/re_tools.py 的 rpak／xmldec 做法。
- 轉職技能分頁（classSkills）：見 classskills.py 檔頭。
- 狀態圖示分頁（status）：見 statusicons.py 檔頭。
"""
import argparse
from collections import Counter
import configparser
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

BUILD = os.path.abspath(os.path.dirname(__file__))
OUT = os.path.dirname(BUILD)  # GameAssets\icon-compare；--out 可改
GAMELAUNCHER = os.environ.get('GA_GAMELAUNCHER', r'D:\程式碼測試區\C++\GameLauncher')
CLIENT = os.environ.get('GA_CLIENT', os.path.join(GAMELAUNCHER, 'client'))
BACKEND = os.environ.get('GA_BACKEND', r'C:\Users\eric7\eclipse-workspace\Lineage381C')
DB = os.path.join(BACKEND, 'db')
RCLIENT = os.environ.get('GA_RCLIENT', r'D:\天堂資料\天堂-主程式懶人包\天堂R\LineageRemastered')

sys.path.insert(0, os.path.join(GAMELAUNCHER, 'tools', 're'))
sys.path.insert(0, BUILD)
import re_tools  # noqa: E402
import pb  # noqa: E402
import classskills  # noqa: E402
import statusicons  # noqa: E402

BACKEND_SRC = os.path.join(BACKEND, 'src')


# ---------- 381 SQL ----------

def sql_values(text):
    """解析 VALUES (...) 括號裡的值：字串（反斜線跳脫）、數字、null。"""
    vals = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "'":
            i += 1
            buf = []
            while i < n:
                c = text[i]
                if c == '\\' and i + 1 < n:
                    buf.append({'n': '\n', 'r': '\r', 't': '\t', '0': '\0'}.get(text[i + 1], text[i + 1]))
                    i += 2
                    continue
                if c == "'":
                    if i + 1 < n and text[i + 1] == "'":
                        buf.append("'")
                        i += 2
                        continue
                    break
                buf.append(c)
                i += 1
            vals.append(''.join(buf))
            i += 1
        elif c in ', ':
            i += 1
        else:
            j = i
            while j < n and text[j] not in ',':
                j += 1
            tok = text[i:j].strip()
            vals.append(None if tok.lower() == 'null' else tok)
            i = j
    return vals


def sql_table(path, table):
    """回傳 [dict(欄位名→值)]。欄位順序取自 CREATE TABLE。"""
    cols = []
    rows = []
    in_create = False
    prefix = 'INSERT INTO %s VALUES (' % table
    prefix2 = 'INSERT INTO `%s` VALUES (' % table
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            if line.startswith('CREATE TABLE `%s` (' % table):
                in_create = True
                cols = []
                continue
            if in_create:
                m = re.match(r'\s+`([^`]+)`', line)
                if m:
                    cols.append(m.group(1))
                elif line.startswith(')'):
                    in_create = False
                continue
            for p in (prefix, prefix2):
                if line.startswith(p):
                    body = line[len(p):].rstrip().rstrip(';').rstrip(')')
                    rows.append(dict(zip(cols, sql_values(body))))
    return rows


# ---------- 名稱正規化 ----------

def clean_name(s):
    """顯示用：去掉 R 的顏色碼（\\aX、\\fX）與前後空白。"""
    s = re.sub(r'\\[aAfF].', '', s or '')
    return s.strip()


# ---------- R 資料 ----------

def rpak_read(idx_path, names):
    """從 R .idx/.pak 讀指定路徑（不分大小寫），回傳 {小寫路徑: bytes}。"""
    import brotli
    want = {n.lower() for n in names}
    out = {}
    with open(idx_path[:-4] + '.pak', 'rb') as pak:
        for name, offset, size, packed, flags in re_tools.rpak_entries(idx_path):
            key = name.lower()
            if key not in want:
                continue
            pak.seek(offset)
            raw = pak.read(packed if flags != 0 else size)
            if flags == 2:
                raw = brotli.decompress(raw)
            out[key] = raw
    return out


def xml_decrypt(data):
    """同 re_tools.cmd_xmldec：開頭 X?xm 的 AES-128-CBC 加密 XML。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if data[:1] == b'<':
        return data
    body = data[4:]
    whole = len(body) // 16 * 16
    dec = Cipher(algorithms.AES(re_tools.XML_KEY), modes.CBC(re_tools.XML_IV)).decryptor()
    head = dec.update(body[:whole]) + dec.finalize() if whole else b''
    chain = body[whole - 16:whole] if whole else re_tools.XML_IV
    tail = bytes(b ^ chain[i] for i, b in enumerate(body[whole:]))
    return b'<' + data[1:4] + head + tail


def r_desc_resolver(desc_bytes):
    lines = desc_bytes.decode('cp950', 'replace').split('\r\n')

    def resolve(s):
        def one(m):
            k = int(m.group(1)) + 1
            return lines[k] if k < len(lines) else m.group(0)
        return re.sub(r'\$(\d+)', one, s)
    return resolve


def r_items(blob, resolve):
    items = []
    for f, t, v in pb.fields(blob):
        if f != 2 or t != 2:
            continue
        inner = pb.fields(v)
        rec_bytes = None
        for ff, tt, vv in inner:
            if ff == 2 and tt == 2:
                rec_bytes = vv
        if rec_bytes is None:
            continue
        m = {}
        for ff, tt, vv in pb.fields(rec_bytes):
            m.setdefault(ff, vv)
        raw = m.get(5) or m.get(4) or b''
        name = clean_name(resolve(raw.decode('utf-8', 'replace')))
        if not name:
            continue
        items.append({'id': m.get(1, 0), 'icon': m.get(2, 0), 'name': name})
    return items


def r_spells(spell_xml, passive_xml):
    out = []
    for s in ET.fromstring(spell_xml).iter('spell'):
        name = clean_name(s.get('name'))
        if name:
            out.append({'id': 'A' + s.get('id', ''), 'icon': int(s.get('icon') or 0), 'name': name})
    for e in ET.fromstring(passive_xml).iter('element'):
        name = clean_name(e.get('name'))
        if name:
            out.append({'id': 'P' + e.get('id', ''), 'icon': int(e.get('icon') or 0), 'name': name})
    return out


def pair_icons(left, right, have_left, have_right):
    """依圖片編號配對實際圖片；名稱和引用數僅依各側資料記錄。"""
    def counts(entries):
        result = {}
        names = {}
        for entry in entries:
            icon = entry['icon']
            if icon > 0:
                result[icon] = result.get(icon, 0) + 1
                name = clean_name(entry['name'])
                if name and name not in names.setdefault(icon, []):
                    names[icon].append(name)
        return result, names

    left_counts, left_names = counts(left)
    right_counts, right_names = counts(right)
    rows = []
    for icon in sorted(set(left_counts) | set(right_counts)):
        a_count = left_counts.get(icon, 0)
        b_count = right_counts.get(icon, 0)
        a = {'icon': icon if icon in have_left else 0, 'iconNo': icon, 'count': a_count,
             'names': left_names.get(icon, [])}
        b = {'icon': icon if icon in have_right else 0, 'iconNo': icon, 'count': b_count,
             'names': right_names.get(icon, [])}
        rows.append({
            'k': 'both' if a['icon'] and b['icon'] else (
                '381' if a['icon'] else ('r' if b['icon'] else 'missing')),
            'a': a,
            'b': b,
        })
    return rows


def import_bmp(source):
    """匯入 LineageTool BMP，依 INI 背景色轉透明 PNG，不覆蓋既有圖片。"""
    from PIL import Image

    if not os.path.isdir(source):
        raise NotADirectoryError(source)
    paths = sorted(n for n in os.listdir(source) if n.lower().endswith('.bmp'))
    if not paths:
        raise ValueError('來源資料夾沒有 BMP 圖片：%s' % source)
    output = os.path.join(OUT, 'img', '381')
    os.makedirs(output, exist_ok=True)
    imported = 0
    skipped = 0
    for name in paths:
        stem = os.path.splitext(name)[0]
        if not re.fullmatch(r'(?:0|[1-9]\d*)c?', stem, re.IGNORECASE):
            raise ValueError('圖檔名稱不是數字或數字加 c：%s' % name)
        dest = os.path.join(output, stem + '.png')
        if os.path.exists(dest):
            skipped += 1
            continue
        ini = os.path.join(source, stem + '.ini')
        config = configparser.ConfigParser()
        with open(ini, encoding='ascii') as f:
            config.read_file(f)
        key = config.getint('LineageTool', 'BackgroundColor')
        if key < 0 or key > 0x7fff:
            raise ValueError('背景色超出 RGB555 範圍：%s' % ini)
        background = tuple(((key >> shift) & 31) * 255 // 31 for shift in (10, 5, 0))
        with Image.open(os.path.join(source, name)) as image:
            if image.format != 'BMP':
                raise ValueError('不是 BMP 圖片：%s' % name)
            rgba = image.convert('RGBA')
            rgba.putdata([(r, g, b, 0 if (r, g, b) == background else 255)
                          for r, g, b, _ in rgba.getdata()])
            encoded = io.BytesIO()
            rgba.save(encoded, format='PNG')
        try:
            with open(dest, 'xb') as f:
                f.write(encoded.getvalue())
        except FileExistsError:
            skipped += 1
            continue
        imported += 1
    print('BMP：%d 張；匯入 %d 張，保留既有 %d 張' % (len(paths), imported, skipped))


def style_preview(out_dir, icons, destination):
    """依指定 R 圖取色，將 381 圖做規則式 46×46 試作；不改原始圖片。"""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

    icons = list(dict.fromkeys(icons))
    if not icons or any(icon <= 0 for icon in icons):
        raise ValueError('請提供正數圖號')
    destination = os.path.abspath(destination)
    real_destination = os.path.normcase(os.path.realpath(destination))
    for folder in ('381', 'r'):
        source_dir = os.path.normcase(os.path.realpath(os.path.join(out_dir, 'img', folder)))
        if real_destination == source_dir or real_destination.startswith(source_dir + os.sep):
            raise ValueError('預覽輸出不可放在原始圖片資料夾：%s' % destination)
    paths = [os.path.join(destination, '%d.png' % icon) for icon in icons]
    paths.append(os.path.join(destination, 'preview.png'))
    sources = []
    for icon in icons:
        source = os.path.join(out_dir, 'img', '381', '%d.png' % icon)
        reference = os.path.join(out_dir, 'img', 'r', '%d.png' % icon)
        for path in (source, reference):
            if not os.path.isfile(path):
                raise FileNotFoundError('找不到圖檔：%s' % path)
        sources.extend((source, reference))
    for path in paths:
        if os.path.exists(path) or os.path.normcase(path) in {os.path.normcase(p) for p in sources}:
            raise FileExistsError('輸出檔已存在，請選擇其他預覽目錄：%s' % path)

    previews = []
    colors = []
    for icon in icons:
        source = os.path.join(out_dir, 'img', '381', '%d.png' % icon)
        reference = os.path.join(out_dir, 'img', 'r', '%d.png' % icon)
        with Image.open(source) as image:
            old = image.convert('RGBA')
        with Image.open(reference) as image:
            ref = image.convert('RGBA')
        candidates = Counter((r, g, b) for r, g, b, a in ref.getdata()
                             if a >= 48 and max(r, g, b) - min(r, g, b) >= 35 and max(r, g, b) < 230)
        if not candidates:
            raise ValueError('R 圖沒有足夠的彩色像素可擷取底色：%d' % icon)
        glow_color = candidates.most_common(1)[0][0]
        foreground = Counter((r // 16, g // 16, b // 16) for r, g, b, a in ref.getdata()
                             if a >= 192 and min(r, g, b) >= 140 and max(r, g, b) >= 200
                             and max(r, g, b) - min(r, g, b) >= 12)
        if not foreground:
            raise ValueError('R 圖沒有足夠的亮色圖案像素可取色：%d' % icon)
        bucket = foreground.most_common(1)[0][0]
        samples = [(r, g, b) for r, g, b, a in ref.getdata()
                   if a >= 192 and min(r, g, b) >= 140 and max(r, g, b) >= 200
                   and max(r, g, b) - min(r, g, b) >= 12
                   and (r // 16, g // 16, b // 16) == bucket]
        symbol_color = tuple(sum(pixel[i] for pixel in samples) // len(samples) for i in range(3))
        highlights = [(r, g, b) for r, g, b, a in ref.getdata() if a >= 200]
        if not highlights:
            raise ValueError('R 圖沒有高不透明度像素可擷取高光：%d' % icon)
        highlights.sort(key=lambda pixel: 2126 * pixel[0] + 7152 * pixel[1] + 722 * pixel[2])
        highlights = highlights[len(highlights) * 9 // 10:]
        highlight_color = tuple(sum(pixel[i] for pixel in highlights) // len(highlights) for i in range(3))
        colors.append((glow_color, symbol_color, highlight_color))
        luminance = ImageOps.grayscale(old.convert('RGB'))
        symbol = luminance.point(lambda value: max(0, min(255, (value - 145) * 255 // 95)))
        symbol = ImageChops.multiply(symbol, old.getchannel('A'))
        if symbol.width > 8 and symbol.height > 8:
            draw = ImageDraw.Draw(symbol)
            draw.rectangle((0, 0, symbol.width - 1, 1), fill=0)
            draw.rectangle((0, symbol.height - 2, symbol.width - 1, symbol.height - 1), fill=0)
            draw.rectangle((0, 0, 1, symbol.height - 1), fill=0)
            draw.rectangle((symbol.width - 2, 0, symbol.width - 1, symbol.height - 1), fill=0)
        if symbol.getbbox() is None:
            raise ValueError('無法從 381 圖擷取亮色圖案：%d' % icon)
        symbol = symbol.resize((40, 40), Image.Resampling.LANCZOS)
        full_mask = Image.new('L', (46, 46), 0)
        full_mask.paste(symbol, (3, 3))
        glow = full_mask.filter(ImageFilter.GaussianBlur(2)).point(lambda value: min(255, value * 2))
        result = Image.new('RGBA', (46, 46), glow_color + (0,))
        result.putalpha(glow)
        foreground_image = Image.new('RGBA', (46, 46), symbol_color + (0,))
        foreground_image.putalpha(full_mask.point(lambda value: min(255, value * 3 // 2)))
        result.alpha_composite(foreground_image)
        highlights_mask = full_mask.point(lambda value: max(0, min(255, (value - 100) * 255 // 155)))
        highlight_image = Image.new('RGBA', (46, 46), highlight_color + (0,))
        highlight_image.putalpha(highlights_mask)
        result.alpha_composite(highlight_image)
        previews.append((old, ref, result))

    os.makedirs(destination, exist_ok=True)
    cell = 110
    sheet = Image.new('RGB', (cell * 3, 35 + cell * len(icons)), (36, 36, 36))
    draw = ImageDraw.Draw(sheet)
    for x, label in enumerate(('381', 'R reference', 'preview')):
        draw.text((x * cell + 8, 8), label, fill=(255, 255, 255))
    for row, (icon, images) in enumerate(zip(icons, previews)):
        y = 35 + row * cell
        draw.text((3, y + 3), str(icon), fill=(255, 255, 255))
        for col, image in enumerate(images):
            picture = ImageOps.contain(image, (80, 80), method=Image.Resampling.NEAREST)
            sheet.paste(picture, (col * cell + 15, y + 22), picture)
    for path, (_, _, result) in zip(paths, previews):
        with open(path, 'xb') as f:
            result.save(f, format='PNG')
    with open(paths[-1], 'xb') as f:
        sheet.save(f, format='PNG')
    for icon, (glow_color, symbol_color, highlight_color) in zip(icons, colors):
        print('圖號 %d：R 光暈 #%02X%02X%02X，圖案 #%02X%02X%02X，高光 #%02X%02X%02X' %
              ((icon,) + glow_color + symbol_color + highlight_color))
    print('已輸出 %d 張試作及對照圖：%s' % (len(icons), destination))


def center_381(out_dir):
    """置中道具與技能對照列的 381 圖，保留畫布尺寸並備份修改前檔案。"""
    from PIL import Image

    data_path = os.path.join(out_dir, 'data.js')
    with open(data_path, encoding='utf-8') as f:
        text = f.read()
    data = json.loads(text[text.index('=') + 1:].rstrip().rstrip(';'))
    ids = {row['a']['icon'] for category in ('items', 'skills') for row in data[category]
           if row['a'] and row['a']['icon']}
    folder = os.path.join(out_dir, 'img', '381')
    backups = os.path.join(out_dir, '_build', '381-originals')
    changed = 0
    centered = 0
    empty = []
    for icon in sorted(ids):
        path = os.path.join(folder, '%d.png' % icon)
        with open(path, 'rb') as f:
            original = f.read()
        with Image.open(io.BytesIO(original)) as image:
            if image.format != 'PNG':
                raise ValueError('不是 PNG 圖片：%s' % path)
            rgba = image.convert('RGBA')
            bounds = rgba.getchannel('A').getbbox()
            if bounds is None:
                empty.append(icon)
                continue
            left, top, right, bottom = bounds
            x = (rgba.width - (right - left)) // 2
            y = (rgba.height - (bottom - top)) // 2
            if (left, top) == (x, y):
                centered += 1
                continue
            result = Image.new('RGBA', rgba.size, (0, 0, 0, 0))
            result.paste(rgba.crop(bounds), (x, y))
            encoded = io.BytesIO()
            result.save(encoded, format='PNG')
        os.makedirs(backups, exist_ok=True)
        backup = os.path.join(backups, '%d.png' % icon)
        if not os.path.exists(backup):
            with open(backup, 'xb') as f:
                f.write(original)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix='.center-', suffix='.tmp', delete=False) as f:
                temp_path = f.name
                f.write(encoded.getvalue())
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        changed += 1
    print('381 道具／技能圖：%d 張；置中 %d 張，原已置中 %d 張，全透明 %d 張' %
          (len(ids), changed, centered, len(empty)))
    if empty:
        print('全透明圖號（未修改）：', empty)
    print('原圖備份：%s' % backups)


def main():
    os.makedirs(os.path.join(OUT, 'img', '381'), exist_ok=True)
    os.makedirs(os.path.join(OUT, 'img', 'r'), exist_ok=True)

    # 381 道具
    items381 = []
    tables = [(os.path.join(DB, 'etcitem.sql'), 'etcitem', 'etc'),
              (os.path.join(DB, 'renewal_381.sql'), 'weapon', 'weapon'),
              (os.path.join(DB, 'renewal_381.sql'), 'armor', 'armor')]
    for path, table, kind in tables:
        rows = sql_table(path, table)
        print('381 %s：%d 筆' % (table, len(rows)))
        for r in rows:
            name = (r.get('name') or '').strip()
            if not name:
                continue
            items381.append({'id': int(r['item_id']), 'icon': int(r.get('invgfx') or 0), 'name': name, 't': kind})
    items381.sort(key=lambda e: e['id'])

    # 381 技能：skill_id 1～220（spellicon.idx 的範圍＝技能視窗會畫的技能），名稱 none 不算
    si = open(os.path.join(CLIENT, 'spellicon.idx'), 'rb').read()
    spellicon = struct.unpack('<%dH' % (len(si) // 2), si)
    skills381 = []
    skipped = []
    skill_rows = sql_table(os.path.join(DB, 'renewal_381.sql'), 'skills')
    for r in skill_rows:
        sid = int(r['skill_id'])
        name = (r.get('name') or '').strip()
        if not name or name.lower() == 'none':
            continue
        if sid > len(spellicon):
            skipped.append((sid, name))
            continue
        skills381.append({'id': sid, 'icon': spellicon[sid - 1], 'name': name})
    skills381.sort(key=lambda e: e['id'])
    print('381 技能：%d 筆（另有 %d 筆 skill_id > %d 不在 spellicon.idx 範圍，未納入）' % (
        len(skills381), len(skipped), len(spellicon)))

    # R 資料
    data = rpak_read(os.path.join(RCLIENT, 'data.idx'),
                     ['desc-c.tbl', 'contents\\item-common.bin', 'SpellList-c.xml', 'passiveSpells-c.xml',
                      'effectlist2-c.xml', 'string-c.tbl'])
    resolve = r_desc_resolver(data['desc-c.tbl'])
    itemsR = r_items(data['contents\\item-common.bin'], resolve)
    itemsR.sort(key=lambda e: e['id'])
    skillsR = r_spells(xml_decrypt(data['spelllist-c.xml']), xml_decrypt(data['passivespells-c.xml']))
    print('R 道具：%d 筆、R 技能：%d 筆' % (len(itemsR), len(skillsR)))

    # R 圖：icon.idx 的 <圖號>.png
    ricon_idx = os.path.join(RCLIENT, 'icon.idx')
    rnames = {e[0].lower() for e in re_tools.rpak_entries(ricon_idx)}

    # 轉職技能（6000～6059）與 R 候選圖（classskills.py）
    all_r_png = {int(n[:-4]) for n in rnames if re.match(r'^\d+\.png$', n)}
    class_skills, class_need381, class_needR = classskills.build({
        'backend_src': BACKEND_SRC, 'skills_rows': skill_rows, 'client': CLIENT,
        'spell_xml': xml_decrypt(data['spelllist-c.xml']), 'passive_xml': xml_decrypt(data['passivespells-c.xml']),
        'effect_xml_r': data['effectlist2-c.xml'], 'string_r': data['string-c.tbl'],
        'xml_decrypt': xml_decrypt, 'have_r': all_r_png,
    })
    print('轉職技能：%d 個' % len(class_skills))

    # 狀態圖示（statusicons.py）：先取兩邊要的圖號，匯出圖之後再配對
    # rstatus_map：GameLauncher 的改號例外表（381 圖號 → R 圖號），建置時套用
    status_ctx = {
        'client': CLIENT, 'read_381_pak': classskills.read_381_pak, 'parse_effects': classskills.parse_effects,
        'xml_decrypt': xml_decrypt, 'effect_xml_r': data['effectlist2-c.xml'], 'string_r': data['string-c.tbl'],
        'rstatus_map': os.path.join(GAMELAUNCHER, 'EditXml', 'RStatusIconMap.xml'),
    }
    _, status_need381, status_needR, _ = statusicons.build(status_ctx, None, None)

    # 狀態圖改號（2026-11-07 使用者定）：381 圖號直接改成 R 圖號。
    # 圖檔把 img/381/<原圖號>.png 複製成 <新圖號>.png；新圖號不從 381 原生 tbt 匯出（避免拿到不同內容的同號圖）。
    status_renum = statusicons.load_renum(status_ctx)
    renum_targets = set(status_renum.values())
    conflict = renum_targets & {e['icon'] for e in items381 + skills381 if e['icon'] > 0}
    if conflict:
        raise RuntimeError('RStatusIconMap 改號目標圖號與 381 道具／技能原生圖號衝突：%s' % sorted(conflict))

    # 381 圖：export-icon-pngs.exe（Sprite.pak、Sprite00～15 的 <圖號>.tbt）
    # 狀態列圖號（status_need381 | status_needR）兩邊的圖都抽：R 有效果用到、381 有同號圖的也要（對稱顯示）；
    # 改號目標圖號除外（用複製的，不用 381 原生同號圖）
    comparison_ids = {e['icon'] for e in items381 + skills381 + itemsR + skillsR if e['icon'] > 0}
    ids = sorted((comparison_ids | class_need381 | status_need381 | status_needR) - renum_targets)
    ids_path = os.path.join(BUILD, 'ids381.txt')
    with open(ids_path, 'w') as f:
        f.write('\n'.join(str(i) for i in ids))
    exe = os.path.join(BUILD, 'export-icon-pngs.exe')
    subprocess.run([exe, CLIENT, ids_path, os.path.join(OUT, 'img', '381'), ''], check=True)
    # 改號圖檔：複製原圖號內容到新圖號（原圖號保留：道具分頁等可能還在用）
    import shutil
    for n, m in sorted(status_renum.items()):
        src_png = os.path.join(OUT, 'img', '381', '%d.png' % n)
        dst_png = os.path.join(OUT, 'img', '381', '%d.png' % m)
        if not os.path.isfile(src_png):
            continue
        if os.path.isfile(dst_png):
            with open(src_png, 'rb') as f:
                s_bytes = f.read()
            with open(dst_png, 'rb') as f:
                if f.read() != s_bytes:
                    # 目標圖號有 381 原生圖（內容不同）：保留原生圖。
                    # 改號來源本來就是同圖的新舊版（例 481→5397，381 的 5397 也是加速同圖），不視為衝突。
                    print('改號圖檔：img/381/%d.png 已存在原生圖，保留原生、不覆蓋（來源 %d.png）' % (m, n))
            continue
        shutil.copyfile(src_png, dst_png)
        print('改號圖檔：img/381/%d.png → %d.png' % (n, m))
    have381 = {int(n[:-4]) for n in os.listdir(os.path.join(OUT, 'img', '381'))
               if re.fullmatch(r'\d+\.png', n)}
    for c in class_skills:
        if c['status381']['icon'] and c['status381']['icon'] not in have381:
            c['status381']['missing'] = True
    # R 根目錄的 icon\ 散檔（18 張）只在 icon.idx 沒有該圖號時補用（散檔與封包的優先順序未確定）
    loose_dir = os.path.join(RCLIENT, 'icon')
    loose = {n.lower() for n in os.listdir(loose_dir)} if os.path.isdir(loose_dir) else set()
    available_r = {i for i in comparison_ids
                   if ('%d.png' % i) in rnames or ('%d.png' % i) in loose}
    status_row_icons = status_need381 | status_needR
    status_available_r = {i for i in status_row_icons if ('%d.png' % i) in rnames or ('%d.png' % i) in loose}

    need_r = available_r | class_needR | status_available_r
    todo = [i for i in sorted(need_r) if not os.path.exists(os.path.join(OUT, 'img', 'r', '%d.png' % i))]
    if todo:
        got = rpak_read(ricon_idx, ['%d.png' % i for i in todo])
        for key, raw in got.items():
            with open(os.path.join(OUT, 'img', 'r', key), 'wb') as f:
                f.write(raw)
        for i in todo:
            key = '%d.png' % i
            if key not in got and key in loose:
                import shutil
                shutil.copyfile(os.path.join(loose_dir, key), os.path.join(OUT, 'img', 'r', key))
                print('R 圖 %s 取自 icon\\ 散檔' % key)
    missing_r = [i for i in sorted(need_r) if not os.path.isfile(os.path.join(OUT, 'img', 'r', '%d.png' % i))]
    if missing_r:
        raise RuntimeError('R 圖未能匯出：%s' % missing_r)
    haveR = available_r
    print('R 圖：需要 %d 張，這次解出 %d 張' % (len(need_r), len(todo)))

    # 只留下頁面會用到的圖號
    need381 = comparison_ids & have381
    need381 |= {i for i in class_need381 if i in have381}
    need381 |= {i for i in status_row_icons if i in have381}

    status_rows, _, _, status_meta = statusicons.build(status_ctx, have381, status_available_r)
    print('狀態圖示：%d 列' % len(status_rows))

    out = {
        'items': pair_icons(items381, itemsR, have381, haveR),
        'skills': pair_icons(skills381, skillsR, have381, haveR),
        'classSkills': class_skills,
        'status': status_rows,
        'statusMeta': status_meta,
        'meta': {
            'src381Items': 'etcitem.sql(etcitem) + renewal_381.sql(weapon, armor)',
            'src381Skills': 'renewal_381.sql(skills) skill_id 1-%d + client/spellicon.idx' % len(spellicon),
            'srcR': 'data.idx: contents/item-common.bin, desc-c.tbl, SpellList-c.xml, passiveSpells-c.xml; icon.idx',
            'raw': {'items381': len(items381), 'skills381': len(skills381),
                    'itemsR': len(itemsR), 'skillsR': len(skillsR)},
            'skipped381Skills': len(skipped),
        },
    }
    for key in ('items', 'skills', 'status'):
        c = {'both': 0, '381': 0, 'r': 0, 'missing': 0}
        for row in out[key]:
            c[row['k']] += 1
        out['meta'][key + 'Count'] = c
        print(key, c)
    with open(os.path.join(OUT, 'data.js'), 'w', encoding='utf-8') as f:
        f.write('// 由 _build/build.py 產生，不要手改\n')
        f.write('window.ICON_DATA = ')
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')
    print('381 圖：用到 %d 張；保留未引用的圖片' % len(need381))
    # 2026-11-06：img/r 若有建置不會產生的檔（例如整包解出的 <編號>_d.png、list、Thumbs.db），
    # 表示有人放了完整的 icon.idx 內容，整個略過清理，不刪任何檔。
    rdir = os.path.join(OUT, 'img', 'r')
    foreign = [n for n in os.listdir(rdir) if not re.fullmatch(r'\d+\.png', n)]
    if foreign:
        print('R 圖：img/r 有 %d 個非建置檔（例 %s），略過清理未用圖' % (len(foreign), foreign[0]))
    else:
        removed = 0
        for n in os.listdir(rdir):
            if int(n[:-4]) not in need_r:
                os.remove(os.path.join(rdir, n))
                removed += 1
        print('R 圖：保留 %d 張，移除未用 %d 張' % (len(need_r), removed))
    os.makedirs(os.path.join(OUT, '_build'), exist_ok=True)
    with open(os.path.join(OUT, '_build', 'skipped381skills.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join('%d\t%s' % s for s in sorted(skipped)))


def check(out_dir):
    """掃 data.js：index.html 會畫的圖（img/381、img/r）是否都在，img 裡有沒有沒被引用的圖。回傳缺圖數。"""
    with open(os.path.join(out_dir, 'data.js'), encoding='utf-8') as f:
        text = f.read()
    data = json.loads(text[text.index('=') + 1:].rstrip().rstrip(';'))
    need = {'381': set(), 'r': set()}
    for key in ('items', 'skills', 'status'):
        for row in data[key]:
            if row['a'] and row['a']['icon']:
                need['381'].add(row['a']['icon'])
            if row['b'] and row['b']['icon']:
                need['r'].add(row['b']['icon'])
    for c in data['classSkills']:
        st = c['status381']
        if st['icon'] and not st.get('missing'):
            need['381'].add(st['icon'])
        for cand in c['skillC'] + c['statusC']:
            if cand['icon']:
                need['r'].add(cand['icon'])
    total_missing = 0
    for d in ('381', 'r'):
        folder = os.path.join(out_dir, 'img', d)
        have = set()
        if os.path.isdir(folder):
            have = {int(n[:-4]) for n in os.listdir(folder) if re.match(r'^\d+\.png$', n)}
        missing = sorted(need[d] - have)
        unused = sorted(have - need[d])
        total_missing += len(missing)
        print('img/%s：引用 %d 張、存在 %d 張、缺 %d 張、未引用 %d 張' % (d, len(need[d]), len(have), len(missing), len(unused)))
        if missing:
            print('  缺：', missing[:50])
        if unused:
            print('  未引用：', unused[:50])
    return total_missing


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='381 → R 圖示對照網頁建置')
    ap.add_argument('--out', help='輸出資料夾（預設 GameAssets\\icon-compare）')
    actions = ap.add_mutually_exclusive_group()
    actions.add_argument('--check', action='store_true', help='只檢查 data.js 引用的圖')
    actions.add_argument('--import-bmp', metavar='資料夾', help='匯入 LineageTool BMP 與 INI，不覆蓋既有 381 PNG（需要 Pillow）')
    actions.add_argument('--center-381', action='store_true', help='置中道具及技能對照的 381 PNG，備份原圖（需要 Pillow）')
    actions.add_argument('--style-preview', metavar='圖號', nargs='+', type=int,
                         help='依同圖號 R 圖取色，試作 381 圖並產生對照圖（需要 Pillow）')
    ap.add_argument('--style-output', help='風格試作輸出資料夾（搭配 --style-preview）')
    args = ap.parse_args()
    if args.style_output and not args.style_preview:
        ap.error('--style-output 必須搭配 --style-preview')
    if args.out:
        OUT = os.path.abspath(args.out)
    if args.check:
        sys.exit(1 if check(OUT) else 0)
    if args.import_bmp:
        import_bmp(args.import_bmp)
        sys.exit(0)
    if args.center_381:
        center_381(OUT)
        sys.exit(0)
    if args.style_preview:
        if not args.style_output:
            ap.error('--style-preview 必須搭配 --style-output')
        style_preview(OUT, args.style_preview, args.style_output)
        sys.exit(0)
    main()
    check(OUT)
