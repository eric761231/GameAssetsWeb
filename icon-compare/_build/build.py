# -*- coding: utf-8 -*-
r"""381 → R 版道具、技能圖示對照網頁的建置腳本（2026-11-04）。

2026-11-06 從 GameLauncher\artifacts\icon-compare 搬到 GameAssets 專案。

重跑：在 GameAssets 根目錄執行
    python icon-compare/_build/build.py              產生 icon-compare/data.js、img/{items,skills,status}/381-<圖號>.png 與 R-<圖號>.png
    python icon-compare/_build/build.py --out <資料夾> 輸出到別的資料夾（例如驗證能否從零重建）
    python icon-compare/_build/build.py --check       只檢查 data.js 引用的圖是否都在三個分類資料夾、有沒有沒被引用的圖
    python icon-compare/_build/build.py --split-img  依現有 data.js 從 img/381、img/r 複製到三個分類資料夾（不解包）
    python icon-compare/_build/build.py --import-assets  掃天堂R主程式地圖並畫縮圖、從 R 版 _PakOutput 複製 UI／立繪 PNG
    python icon-compare/_build/build.py --drive-index <清單.json>  從 rclone lsjson 清單建立 Drive 圖片索引

來源路徑（全部唯讀，可用環境變數改）：
    GA_GAMELAUNCHER  GameLauncher 專案（預設 D:\程式碼測試區\C++\GameLauncher；用它的 tools/re/re_tools.py）
    GA_CLIENT        381 客戶端資源（預設 <GA_GAMELAUNCHER>\client）
    GA_BACKEND       381 Java 後端（預設 C:\Users\eric7\eclipse-workspace\Lineage381C；用 db\*.sql、src\）
    GA_RCLIENT       天堂R 客戶端（預設 D:\天堂資料\天堂-主程式懶人包\天堂R\LineageRemastered）
    GA_MAPEDITOR     MapEditor 專案（預設 D:\\程式碼測試區\\C++\\MapEditor；讀 mapnames.txt 與 MapEditor.ini 的 client）
    GA_R_PAKOUTPUT   天堂R LineageTool 解包（預設桌面 LineageTool_20230627\\_PakOutput；UI 與 Image/PO_）

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
import html as html_lib
import io
import json
import os
import re
import shutil
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
MAPEDITOR = os.environ.get('GA_MAPEDITOR', r'D:\程式碼測試區\C++\MapEditor')
R_PAKOUTPUT = os.environ.get(
    'GA_R_PAKOUTPUT',
    os.path.join(os.path.expanduser('~'), 'OneDrive', '桌面', 'LineageTool_20230627', '_PakOutput'))
MAP_BLOCK_SIZE = 64
MAP_BLOCK_BIAS = 0x7e00

sys.path.insert(0, os.path.join(GAMELAUNCHER, 'tools', 're'))
sys.path.insert(0, BUILD)
import re_tools  # noqa: E402
import pb  # noqa: E402
import classskills  # noqa: E402
import mappreview  # noqa: E402
import statusicons  # noqa: E402

BACKEND_SRC = os.path.join(BACKEND, 'src')
IMG_CATS = ('items', 'skills', 'status')


def load_icon_data(out_dir):
    path = os.path.join(out_dir, 'data.js')
    with open(path, encoding='utf-8') as f:
        text = f.read()
    return json.loads(text[text.index('=') + 1:].rstrip().rstrip(';'))


def icons_from_payload(data):
    """items／skills／status／classSkills 引用的圖號。轉職技能全部算技能。icon 為 0 或 missing 不收。"""
    need = {cat: {'381': set(), 'r': set()} for cat in IMG_CATS}
    for cat in IMG_CATS:
        for row in data.get(cat, []):
            a = row.get('a')
            if a and a.get('icon'):
                need[cat]['381'].add(a['icon'])
            b = row.get('b')
            if b and b.get('icon'):
                need[cat]['r'].add(b['icon'])
    for c in data.get('classSkills', []):
        st = c.get('status381') or {}
        if st.get('icon') and not st.get('missing'):
            need['skills']['381'].add(st['icon'])
        for cand in (c.get('skillC') or []) + (c.get('statusC') or []):
            if cand.get('icon'):
                need['skills']['r'].add(cand['icon'])
    return need


def cat_png(side, icon):
    prefix = '381' if side == '381' else 'R'
    return '%s-%d.png' % (prefix, icon)


def copy_to_categories(out_dir, need):
    """從 img/381、img/r 複製到 items／skills／status，檔名加 381-／R- 前綴。不覆蓋既有檔。"""
    copied = {cat: {'381': 0, 'r': 0} for cat in IMG_CATS}
    missing_src = []
    for cat in IMG_CATS:
        dest_dir = os.path.join(out_dir, 'img', cat)
        os.makedirs(dest_dir, exist_ok=True)
        for side, legacy in (('381', '381'), ('r', 'r')):
            src_dir = os.path.join(out_dir, 'img', legacy)
            for icon in sorted(need[cat][side]):
                dst = os.path.join(dest_dir, cat_png(side, icon))
                if os.path.isfile(dst):
                    continue
                src = os.path.join(src_dir, '%d.png' % icon)
                if not os.path.isfile(src):
                    missing_src.append((cat, side, icon))
                    continue
                shutil.copyfile(src, dst)
                copied[cat][side] += 1
    return copied, missing_src


def list_legacy_png(folder):
    """舊 img/381、img/r：純數字.png 的圖號；其他檔名當未確定。"""
    ids = set()
    other = []
    if not os.path.isdir(folder):
        return ids, other
    for n in os.listdir(folder):
        if re.fullmatch(r'\d+\.png', n):
            ids.add(int(n[:-4]))
        else:
            other.append(n)
    return ids, other


def overlap_icons(need):
    """圖號出現在兩個以上分類。per_side 分 381／R；union 為兩側圖號聯集後再跨類。"""
    per_side = {}
    for side in ('381', 'r'):
        where = {}
        for cat in IMG_CATS:
            for icon in need[cat][side]:
                where.setdefault(icon, []).append(cat)
        per_side[side] = {i: cats for i, cats in where.items() if len(cats) >= 2}
    union_where = {}
    for cat in IMG_CATS:
        for icon in need[cat]['381'] | need[cat]['r']:
            union_where.setdefault(icon, []).append(cat)
    union = {i: cats for i, cats in union_where.items() if len(cats) >= 2}
    return per_side, union


def print_split_stats(out_dir, need, copied, missing_src):
    print('分類圖複製：items 381=%d R=%d；skills 381=%d R=%d；status 381=%d R=%d' % (
        copied['items']['381'], copied['items']['r'],
        copied['skills']['381'], copied['skills']['r'],
        copied['status']['381'], copied['status']['r']))
    for cat in IMG_CATS:
        folder = os.path.join(out_dir, 'img', cat)
        n381 = n_r = extra = 0
        if os.path.isdir(folder):
            for name in os.listdir(folder):
                if re.fullmatch(r'381-\d+\.png', name):
                    n381 += 1
                elif re.fullmatch(r'R-\d+\.png', name):
                    n_r += 1
                else:
                    extra += 1
        print('img/%s：381- %d 張、R- %d 張%s' % (
            cat, n381, n_r, '、未確定檔名 %d' % extra if extra else ''))
    per_side, union = overlap_icons(need)
    print('多類重疊：分類聯集 %d 個圖號；381 側 %d、R 側 %d' % (
        len(union), len(per_side['381']), len(per_side['r'])))
    ref381 = set()
    ref_r = set()
    for cat in IMG_CATS:
        ref381 |= need[cat]['381']
        ref_r |= need[cat]['r']
    have381, other381 = list_legacy_png(os.path.join(out_dir, 'img', '381'))
    have_r, other_r = list_legacy_png(os.path.join(out_dir, 'img', 'r'))
    unused381 = sorted(have381 - ref381)
    unused_r = sorted(have_r - ref_r)
    print('舊 img/381：%d 張數字圖、未引用 %d 張；舊 img/r：%d 張數字圖、未引用 %d 張（舊資料夾未刪）' % (
        len(have381), len(unused381), len(have_r), len(unused_r)))
    if other381:
        print('舊 img/381 未確定檔名 %d：%s' % (len(other381), other381[:20]))
    if other_r:
        print('舊 img/r 未確定檔名 %d：%s' % (len(other_r), other_r[:20]))
    if missing_src:
        print('缺來源（未複製）%d：%s' % (len(missing_src), missing_src[:50]))
    return unused381, unused_r, union


def split_existing(out_dir):
    """依現有 data.js 從 img/381、img/r 複製到三個分類資料夾，不解包。"""
    data = load_icon_data(out_dir)
    need = icons_from_payload(data)
    copied, missing_src = copy_to_categories(out_dir, need)
    print_split_stats(out_dir, need, copied, missing_src)
    return 1 if missing_src else 0


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
    for name in ('381', 'r') + IMG_CATS:
        os.makedirs(os.path.join(OUT, 'img', name), exist_ok=True)

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
    # 圖檔把 img/381/<原圖號>.png 複製成 img/381/<新圖號>.png（解包快取）以及 img/status/381-<新圖號>.png；
    # 新圖號不從 381 原生 tbt 匯出（避免拿到不同內容的同號圖）。
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
    for n, m in sorted(status_renum.items()):
        src_png = os.path.join(OUT, 'img', '381', '%d.png' % n)
        dst_png = os.path.join(OUT, 'img', '381', '%d.png' % m)
        dst_status = os.path.join(OUT, 'img', 'status', '381-%d.png' % m)
        if not os.path.isfile(src_png):
            continue
        with open(src_png, 'rb') as f:
            s_bytes = f.read()
        if os.path.isfile(dst_png):
            with open(dst_png, 'rb') as f:
                if f.read() != s_bytes:
                    # 目標圖號有 381 原生圖（內容不同）：保留原生圖。
                    # 改號來源本來就是同圖的新舊版（例 481→5397，381 的 5397 也是加速同圖），不視為衝突。
                    print('改號圖檔：img/381/%d.png 已存在原生圖，保留原生、不覆蓋（來源 %d.png）' % (m, n))
        else:
            shutil.copyfile(src_png, dst_png)
            print('改號圖檔：img/381/%d.png → %d.png' % (n, m))
        if os.path.isfile(dst_status):
            with open(dst_status, 'rb') as f:
                if f.read() != s_bytes:
                    print('改號圖檔：img/status/381-%d.png 已存在且內容不同，保留既有、不覆蓋（來源 %d.png）' % (m, n))
        else:
            shutil.copyfile(src_png, dst_status)
            print('改號圖檔：img/status/381-%d.png（來源 img/381/%d.png）' % (m, n))
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
    cat_need = icons_from_payload(out)
    copied, missing_src = copy_to_categories(OUT, cat_need)
    print_split_stats(OUT, cat_need, copied, missing_src)
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
    """掃 data.js：分類資料夾 img/{items,skills,status} 的 381-<圖號>.png、R-<圖號>.png 是否都在。回傳缺圖數。"""
    data = load_icon_data(out_dir)
    need = icons_from_payload(data)
    total_missing = 0
    for cat in IMG_CATS:
        folder = os.path.join(out_dir, 'img', cat)
        have = {'381': set(), 'r': set()}
        extra = []
        if os.path.isdir(folder):
            for n in os.listdir(folder):
                m = re.fullmatch(r'(381|R)-(\d+)\.png', n)
                if not m:
                    extra.append(n)
                    continue
                side = '381' if m.group(1) == '381' else 'r'
                have[side].add(int(m.group(2)))
        for side, label in (('381', '381-'), ('r', 'R-')):
            missing = sorted(need[cat][side] - have[side])
            unused = sorted(have[side] - need[cat][side])
            total_missing += len(missing)
            print('img/%s %s：引用 %d 張、存在 %d 張、缺 %d 張、未引用 %d 張' % (
                cat, label, len(need[cat][side]), len(have[side]), len(missing), len(unused)))
            if missing:
                print('  缺：', missing[:50])
            if unused:
                print('  未引用：', unused[:50])
        if extra:
            print('img/%s 未確定檔名 %d：%s' % (cat, len(extra), extra[:50]))
    return total_missing


def write_icon_data(out_dir, data):
    with open(os.path.join(out_dir, 'data.js'), 'w', encoding='utf-8') as f:
        f.write('// 由 _build/build.py 產生，不要手改\n')
        f.write('window.ICON_DATA = ')
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.write(';\n')


def mapeditor_client():
    """MapEditor.ini [paths] client，沒有就用 GA_CLIENT。只讀。"""
    ini = os.path.join(MAPEDITOR, 'MapEditor.ini')
    if os.path.isfile(ini):
        cfg = configparser.ConfigParser()
        with open(ini, 'rb') as f:
            raw = f.read(4)
        enc = 'utf-16' if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff') else 'utf-8'
        cfg.read(ini, encoding=enc)
        if cfg.has_option('paths', 'client'):
            path = cfg.get('paths', 'client').strip()
            if path:
                return path
    return CLIENT


def load_map_names():
    """MapEditor.exe 旁 mapnames.txt：編號<Tab>名稱，# 註解。"""
    path = os.path.join(MAPEDITOR, 'mapnames.txt')
    names = {}
    if not os.path.isfile(path):
        print('地圖名稱：找不到 %s' % path)
        return names
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '\t' not in line:
                continue
            sid, name = line.split('\t', 1)
            if sid.isdigit():
                names[int(sid)] = name.strip()
    return names


def load_r_map_names():
    """R 版 zone3.xml：同一地圖編號的第一個區域名稱。跟 381 的 mapnames 常常不同。

    這份 XML 有一區缺了開頭的 <zone>（傲慢之塔地下神殿），整份丟給 parser 會失敗，
    所以只抓 <name> 後面最近的 <map num>。alias 的連續編號沿用基準地圖的名稱。
    """
    idx = os.path.join(RCLIENT, 'data.idx')
    if not os.path.isfile(idx):
        print('R 地圖名稱：找不到 %s' % idx)
        return {}
    got = rpak_read(idx, ['zone3.xml'])
    raw = got.get('zone3.xml')
    if not raw:
        print('R 地圖名稱：data.idx 沒有 zone3.xml')
        return {}
    text = xml_decrypt(raw).decode('utf-8')
    names = {}
    for match in re.finditer(r'<name>([^<]*)</name>.*?<map\s+num="(\d+)"', text, re.S):
        label = clean_name(match.group(1))
        mid = int(match.group(2))
        if label and mid not in names:
            names[mid] = label
    for match in re.finditer(r'<alias\s+num="(\d+)">(.*?)</alias>', text, re.S):
        base = names.get(int(match.group(1)))
        if not base:
            continue
        for span in re.finditer(r'<map\s+from="(\d+)"\s+to="(\d+)"', match.group(2)):
            start = int(span.group(1))
            end = int(span.group(2))
            if end < start or end - start > 5000:
                continue
            for mid in range(start, end + 1):
                if mid not in names:
                    names[mid] = base
    print('R 地圖名稱：zone3.xml %d 個地圖編號' % len(names))
    return names


def load_sql_map_names():
    """後端 renewal_381.sql 的 mapids.locationname；空字串不當名稱。"""
    path = os.path.join(DB, 'renewal_381.sql')
    names = {}
    if not os.path.isfile(path):
        return names
    for row in sql_table(path, 'mapids'):
        mid = row.get('mapid')
        label = (row.get('locationname') or '').strip()
        if mid is None or mid == '' or not label:
            continue
        names[int(mid)] = label
    return names


def parse_block_stem(stem):
    """8 碼 hex 檔名 → 地圖塊座標，同 MapEditor parseBlockName。"""
    if len(stem) != 8:
        return None
    try:
        int(stem, 16)
    except ValueError:
        return None
    return int(stem[0:4], 16), int(stem[4:8], 16)


def map_block_begin(block_coordinate):
    return (block_coordinate - MAP_BLOCK_BIAS) * MAP_BLOCK_SIZE


def scan_maps():
    """天堂R主程式 map/<純數字>/ 的 .s32／.seg。381 名稱先 mapnames.txt，沒有再查後端；R 名稱用 zone3.xml。不複製地圖塊。"""
    client = RCLIENT
    root = os.path.join(client, 'map')
    names = load_map_names()
    sql_names = load_sql_map_names()
    r_names = load_r_map_names()
    maps = []
    unnamed381 = 0
    from_sql = 0
    unnamed_r = 0
    if not os.path.isdir(root):
        print('地圖：找不到 %s' % root)
        return maps
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        if not name.isdigit() or len(name) > 6:
            continue
        mid = int(name)
        unique = set()
        for fn in os.listdir(folder):
            low = fn.lower()
            if not (low.endswith('.s32') or low.endswith('.seg')):
                continue
            parsed = parse_block_stem(os.path.splitext(fn)[0])
            if parsed:
                unique.add(parsed)
        if not unique:
            continue
        min_bx = min(b[0] for b in unique)
        max_bx = max(b[0] for b in unique)
        min_by = min(b[1] for b in unique)
        max_by = max(b[1] for b in unique)
        label381 = names.get(mid, '')
        src381 = 'mapnames'
        if not label381:
            label381 = sql_names.get(mid, '')
            if label381:
                src381 = 'sql'
                from_sql += 1
        if not label381:
            label381 = '未知'
            src381 = 'unknown'
            unnamed381 += 1
        label_r = r_names.get(mid, '')
        if label_r:
            src_r = 'zone3'
        else:
            label_r = '未知'
            src_r = 'unknown'
            unnamed_r += 1
        row = {
            'id': mid,
            'name381': label381,
            'name381Src': src381,
            'nameR': label_r,
            'nameRSrc': src_r,
            'startX': map_block_begin(min_bx),
            'endX': map_block_begin(max_bx) + 63,
            'startY': map_block_begin(min_by),
            'endY': map_block_begin(max_by) + 63,
        }
        maps.append(row)
    maps.sort(key=lambda r: r['id'])
    print('地圖：client=%s，%d 張（381 mapnames %d、SQL 補 %d、381 未知 %d；R zone3 %d、R 未知 %d）' % (
        client, len(maps), len(maps) - from_sql - unnamed381, from_sql, unnamed381,
        len(maps) - unnamed_r, unnamed_r))
    return maps


def skip_ui_rel(rel):
    parts = rel.replace('\\', '/').split('/')
    for p in parts[:-1]:
        pl = p.lower()
        if pl == 'sample' or pl.startswith('fx_'):
            return True
    return False


def is_portrait_rel(rel):
    path = rel.replace('\\', '/')
    name = path.rsplit('/', 1)[-1]
    if re.fullmatch(r'PO_\d+\.png', name, re.I):
        return True
    if re.search(r'LoginProcess', path, re.I):
        return False
    if re.search(r'(^|/)Portrait', path, re.I):
        return True
    if re.search(r'(^|/)Login', path, re.I):
        return True
    if re.search(r'_Login(/|$)', path, re.I):
        return True
    if re.search(r'/Char/', path):
        return True
    if re.search(r'/CharSelect/', path, re.I):
        return True
    if re.search(r'_[FM](/|$)', path):
        return True
    return False


def prefixed_name(filename):
    if filename.startswith('R-') or filename.startswith('381-'):
        return filename
    return 'R-' + filename


def copy_png_tree(src_root, rel, dest_root):
    src = os.path.join(src_root, rel.replace('/', os.sep))
    dest_rel = '/'.join(rel.replace('\\', '/').split('/')[:-1] + [prefixed_name(os.path.basename(rel))])
    dest = os.path.join(dest_root, dest_rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if not os.path.isfile(dest):
        shutil.copyfile(src, dest)
    return dest_rel, os.path.getsize(dest)


def import_r_images(out_dir):
    """從 R 版 _PakOutput 複製 PNG：UI（略過 Sample／FX_）、立繪（PO_ 與 Spine 登入角色）。"""
    ui_src = os.path.join(R_PAKOUTPUT, 'UI')
    image_src = os.path.join(R_PAKOUTPUT, 'Image')
    ui_rows = []
    portrait_rows = []
    skipped_fx = 0
    if not os.path.isdir(ui_src):
        print('UI：找不到 %s' % ui_src)
    else:
        for dirpath, _dirnames, filenames in os.walk(ui_src):
            for fn in filenames:
                if not fn.lower().endswith('.png'):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), ui_src)
                if skip_ui_rel(rel):
                    skipped_fx += 1
                    continue
                kind = 'portraits' if is_portrait_rel(rel) else 'ui'
                dest_root = os.path.join(out_dir, 'img', kind)
                dest_rel, size = copy_png_tree(ui_src, rel, dest_root)
                folder = dest_rel.rsplit('/', 1)[0] if '/' in dest_rel else ''
                row = {'file': dest_rel, 'folder': folder, 'name': os.path.splitext(fn)[0], 'bytes': size}
                if kind == 'portraits':
                    portrait_rows.append(row)
                else:
                    ui_rows.append(row)
    if os.path.isdir(image_src):
        for fn in os.listdir(image_src):
            if not re.fullmatch(r'PO_\d+\.png', fn, re.I):
                continue
            dest_root = os.path.join(out_dir, 'img', 'portraits')
            dest_rel, size = copy_png_tree(image_src, fn, dest_root)
            portrait_rows.append({
                'file': dest_rel,
                'folder': 'Image',
                'name': os.path.splitext(fn)[0],
                'bytes': size,
            })
    else:
        print('立繪：找不到 %s（PO_*.png 未匯入）' % image_src)
    ui_rows.sort(key=lambda r: (r['folder'], r['name']))
    portrait_rows.sort(key=lambda r: (r['folder'], r['name']))
    print('UI PNG：%d 張（略過 Sample／FX_ %d 張）' % (len(ui_rows), skipped_fx))
    print('立繪 PNG：%d 張' % len(portrait_rows))
    return ui_rows, portrait_rows, skipped_fx


def export_map_overviews(out_dir):
    """用 MapEditor 的圖塊管線畫每張地圖的全貌（最長邊 4096），再縮小成列表縮圖。已有的全貌略過。"""
    exe = os.path.join(MAPEDITOR, 'MapEditor.exe')
    full_dir = os.path.join(out_dir, 'img', 'maps', 'full')
    thumb_dir = os.path.join(out_dir, 'img', 'maps')
    os.makedirs(full_dir, exist_ok=True)
    if not os.path.isfile(exe):
        print('地圖全貌：找不到 %s' % exe)
        return
    print('地圖全貌：%s --client %s overview-all' % (exe, RCLIENT))
    result = subprocess.run([exe, '--client', RCLIENT, 'overview-all', full_dir, '4096'])
    if result.returncode != 0:
        print('地圖全貌：MapEditor 結束碼 %s' % result.returncode)
    try:
        from PIL import Image
    except ImportError:
        print('地圖縮圖：沒有 Pillow，列表仍用舊縮圖')
        return
    made = 0
    for name in os.listdir(full_dir):
        if not name.lower().endswith('.png'):
            continue
        with Image.open(os.path.join(full_dir, name)) as image:
            image.thumbnail((420, 420), Image.Resampling.LANCZOS)
            image.save(os.path.join(thumb_dir, name))
        made += 1
    print('地圖縮圖：由全貌縮小 %d 張' % made)


def import_assets(out_dir):
    data = load_icon_data(out_dir)
    maps = scan_maps()
    mappreview.render(os.path.join(RCLIENT, 'map'), os.path.join(out_dir, 'img', 'maps'), maps)
    export_map_overviews(out_dir)
    data['maps'] = maps
    ui_rows, portrait_rows, skipped_fx = import_r_images(out_dir)
    data['ui'] = ui_rows
    data['portraits'] = portrait_rows
    meta = data.setdefault('meta', {})
    meta['maps'] = len(data['maps'])
    meta['ui'] = len(ui_rows)
    meta['portraits'] = len(portrait_rows)
    meta['uiSkippedFx'] = skipped_fx
    meta['srcMaps'] = '天堂R主程式 map\\<編號>；381 名稱 mapnames.txt／mapids；R 名稱 data.idx zone3.xml'
    meta['srcUi'] = R_PAKOUTPUT
    write_icon_data(out_dir, data)
    print('已寫入 data.js 的 maps／ui／portraits')
    return 0


def import_drive_index(out_dir, listing_path):
    folders = {
        '道具圖': 'items',
        '技能圖': 'skills',
        '狀態圖': 'status',
        '人物圖': 'portraits',
        '地圖': 'maps',
        'UI元件圖': 'ui',
        '381': '381',
        'r': 'r',
    }
    with open(listing_path, encoding='utf-8-sig') as f:
        listing = json.load(f)
    if not isinstance(listing, list):
        raise ValueError('Drive 清單必須是 rclone lsjson 的陣列')
    images = {}
    counts = Counter()
    for entry in listing:
        if not isinstance(entry, dict) or not isinstance(entry.get('Path'), str):
            raise ValueError('Drive 清單包含無效項目')
        parts = entry['Path'].split('/')
        if (len(parts) < 2 or parts[0] not in folders
                or any(part in ('', '.', '..') or '\\' in part for part in parts[1:])
                or not parts[-1].lower().endswith('.png')):
            raise ValueError('未支援的 Drive 圖片路徑：%s' % entry['Path'])
        file_id = entry.get('ID')
        if not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', file_id):
            raise ValueError('Drive 圖片 ID 無效：%s' % entry['Path'])
        path = 'img/%s/%s' % (folders[parts[0]], '/'.join(parts[1:]))
        if path in images:
            raise ValueError('Drive 圖片檔名重複：%s' % path)
        images[path] = file_id
        counts[folders[parts[0]]] += 1
    if not images:
        raise ValueError('Drive 圖片清單為空')
    output = os.path.join(out_dir, 'drive-image-ids.js')
    with open(output, 'w', encoding='utf-8', newline='\n') as f:
        f.write('window.DRIVE_IMAGE_IDS = ')
        json.dump(images, f, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        f.write(';\n')
    print('已寫入 %s：%s' % (output, ', '.join('%s=%d' % item for item in sorted(counts.items()))))
    return 0


DRIVE_MAP_VERSIONS = (('R版地圖', 'r'), ('381版地圖', '381'))


def drive_list_folder(folder_id):
    """公開分享的 Drive 資料夾：讀 embeddedfolderview 頁面，回傳 [(id, 名稱, 是否資料夾)]。不需要授權。"""
    import time
    import urllib.request
    url = 'https://drive.google.com/embeddedfolderview?id=' + folder_id
    last_error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                html = resp.read().decode('utf-8', 'replace')
            break
        except Exception as exc:  # 網路偶發錯誤：重試
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError('讀不到 Drive 資料夾 %s：%s' % (folder_id, last_error))
    entries = []
    for chunk in html.split('class="flip-entry"')[1:]:
        found_id = re.search(r'id="entry-([A-Za-z0-9_-]+)"', chunk)
        found_title = re.search(r'flip-entry-title">([^<]*)<', chunk)
        if not found_id or not found_title:
            continue
        title = html_lib.unescape(found_title.group(1)).strip()
        # 資料夾的連結是 /drive/folders/<id>，檔案是 /file/d/<id>
        is_folder = ('/drive/folders/' + found_id.group(1)) in chunk
        entries.append((found_id.group(1), title, is_folder))
    return entries


def drive_map_version(folder_id):
    """一個版本的資料夾（裡面有 map/、Tile.idx、Tile.pak）→ {'tileIdx', 'tilePak', 'maps': {編號: {檔名: ID}}}。"""
    from concurrent.futures import ThreadPoolExecutor
    top = {title.lower(): (fid, is_folder) for fid, title, is_folder in drive_list_folder(folder_id)}
    for need in ('map', 'tile.idx', 'tile.pak'):
        if need not in top:
            raise RuntimeError('Drive 資料夾 %s 缺少 %s' % (folder_id, need))
    folders = []
    for fid, title, is_folder in drive_list_folder(top['map'][0]):
        if is_folder and title.isdigit() and len(title) <= 6:
            folders.append((int(title), fid))
    print('  map 資料夾 %d 個，逐一列出檔案…' % len(folders))

    def list_blocks(item):
        mid, fid = item
        chosen = {}
        for file_id, name, is_folder in drive_list_folder(fid):
            low = name.lower()
            if is_folder or not (low.endswith('.s32') or low.endswith('.seg')):
                continue
            stem = os.path.splitext(name)[0]
            if parse_block_stem(stem) is None:
                continue
            key = stem.lower()
            # Lin.bin 0x435310：.s32 優先，缺檔才讀同名 .seg（同 MapEditor listMapBlocks）
            if key in chosen and low.endswith('.seg'):
                continue
            chosen[key] = (key + ('.seg' if low.endswith('.seg') else '.s32'), file_id)
        return mid, chosen

    maps = {}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        for mid, chosen in pool.map(list_blocks, folders):
            done += 1
            if chosen:
                maps[str(mid)] = {name: file_id for name, file_id in sorted(chosen.values())}
            if done % 100 == 0 or done == len(folders):
                print('  %d／%d' % (done, len(folders)))
    return {'tileIdx': top['tile.idx'][0], 'tilePak': top['tile.pak'][0], 'maps': maps}


def import_drive_maps(out_dir, root_id):
    """公開的 Drive 地圖根資料夾（R版地圖／381版地圖）→ map-drive-ids.js，給網頁的地圖檢視器分段讀 Tile.pak 與 .s32。"""
    root = {title: fid for fid, title, is_folder in drive_list_folder(root_id) if is_folder}
    result = {}
    for title, key in DRIVE_MAP_VERSIONS:
        if title not in root:
            print('Drive 地圖：根資料夾沒有「%s」，略過' % title)
            continue
        print('Drive 地圖：%s' % title)
        result[key] = drive_map_version(root[title])
        blocks = sum(len(v) for v in result[key]['maps'].values())
        print('  %d 張地圖、%d 個地圖塊' % (len(result[key]['maps']), blocks))
    if not result:
        raise RuntimeError('Drive 地圖根資料夾裡沒有可用的版本資料夾')
    names = load_map_names()
    sql_names = load_sql_map_names()
    names381 = {}
    for key in result:
        for mid in result[key]['maps']:
            label = names.get(int(mid)) or sql_names.get(int(mid))
            if label:
                names381[mid] = label
    result['names381'] = names381
    output = os.path.join(out_dir, 'map-drive-ids.js')
    with open(output, 'w', encoding='utf-8', newline='\n') as f:
        f.write('window.MAP_DRIVE = ')
        json.dump(result, f, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        f.write(';\n')
    print('已寫入 %s' % output)
    write_map_allow(out_dir, result)
    return 0


def write_map_allow(out_dir, result):
    """轉送函式（lib/drive-proxy.js）的白名單：只允許地圖清單裡的 Drive 檔案 ID。"""
    files = set()
    paks = set()
    for value in result.values():
        if not isinstance(value, dict) or 'maps' not in value:
            continue
        files.add(value['tileIdx'])
        paks.add(value['tilePak'])
        for blocks in value['maps'].values():
            files.update(blocks.values())
    output = os.path.join(os.path.dirname(out_dir), 'lib', 'map-drive-allow.json')
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'files': sorted(files), 'paks': sorted(paks)}, f, separators=(',', ':'))
    print('已寫入 %s（%d 個檔案、%d 個 pak）' % (output, len(files), len(paks)))


def export_map_thumbs(out_dir):
    """img/maps/R-<編號>.png（不進 git）→ map-thumbs/R-<編號>.webp（最長邊 360，約 10KB，進 git 給線上地圖列表用）。"""
    from PIL import Image
    src_dir = os.path.join(out_dir, 'img', 'maps')
    dest_dir = os.path.join(out_dir, 'map-thumbs')
    os.makedirs(dest_dir, exist_ok=True)
    made = 0
    kept = 0
    failed = []
    for name in sorted(os.listdir(src_dir)):
        if not (name.startswith('R-') and name.lower().endswith('.png')):
            continue
        src = os.path.join(src_dir, name)
        dest = os.path.join(dest_dir, os.path.splitext(name)[0] + '.webp')
        if os.path.isfile(dest) and os.path.getmtime(dest) >= os.path.getmtime(src):
            kept += 1
            continue
        try:
            with Image.open(src) as image:
                image = image.convert('RGBA')
                image.thumbnail((360, 360), Image.Resampling.LANCZOS)
                image.save(dest, 'WEBP', quality=78, method=4)
            made += 1
        except Exception as exc:
            failed.append('%s：%s' % (name, exc))
    total = sum(os.path.getsize(os.path.join(dest_dir, n)) for n in os.listdir(dest_dir))
    print('地圖縮圖：新寫 %d 張、已有 %d 張，map-thumbs 共 %.1f MB' % (made, kept, total / 1048576.0))
    for line in failed:
        print('  轉不出來 ' + line)
    return 1 if failed else 0


def serve(port):
    """本機開網站（GameAssets 根目錄），並提供跟 Vercel 函式 api/drive.js 相同規則的 /api/drive 轉送，給地圖檢視器測試。"""
    import http.server
    import urllib.parse
    import urllib.request
    root = os.path.dirname(OUT)
    ids_path = os.path.join(OUT, 'map-drive-ids.js')
    with open(ids_path, encoding='utf-8') as f:
        text = f.read()
    data = json.loads(text[text.index('{'):text.rindex('}') + 1])
    files = set()
    paks = set()
    for value in data.values():
        if not isinstance(value, dict) or 'maps' not in value:
            continue
        files.add(value['tileIdx'])
        paks.add(value['tilePak'])
        for blocks in value['maps'].values():
            files.update(blocks.values())
    max_len = 4 * 1024 * 1024

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=root, **kw)

        def fail(self, status, message):
            body = message.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != '/api/drive':
                return super().do_GET()
            q = urllib.parse.parse_qs(parsed.query)
            file_id = (q.get('id') or [''])[0]
            has_range = 'start' in q
            try:
                start = int((q.get('start') or ['0'])[0])
                length = int((q.get('len') or ['0'])[0])
            except ValueError:
                return self.fail(400, '範圍不合理')
            is_pak = file_id in paks
            if not is_pak and file_id not in files:
                return self.fail(404, '不在地圖檔案清單內')
            if has_range and (start < 0 or length <= 0 or length > max_len):
                return self.fail(400, '範圍不合理')
            if not has_range and is_pak:
                return self.fail(400, 'Tile.pak 必須指定範圍')
            req = urllib.request.Request(
                'https://drive.usercontent.google.com/download?id=%s&export=download&confirm=t' % file_id)
            if has_range:
                req.add_header('Range', 'bytes=%d-%d' % (start, start + length - 1))
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    status = resp.status
                    ctype = resp.headers.get('Content-Type', '')
                    body = resp.read()
            except Exception as exc:
                return self.fail(502, 'Drive 連線失敗：%s' % exc)
            if (has_range and status != 206) or (not has_range and status != 200) or 'text/html' in ctype:
                return self.fail(502, 'Drive 回應 %s %s' % (status, ctype))
            if has_range and len(body) != length:
                return self.fail(502, '分段長度不符')
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            self.wfile.write(body)

        def log_request(self, code='-', size='-'):
            # 轉送成功的不印（一張地圖會有上千筆）
            if self.path.startswith('/api/drive') and str(code) == '200':
                return
            super().log_request(code, size)

    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print('網站：http://127.0.0.1:%d/icon-compare/（Ctrl+C 結束）' % port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='381 → R 圖示對照網頁建置')
    ap.add_argument('--out', help='輸出資料夾（預設 GameAssets\\icon-compare）')
    actions = ap.add_mutually_exclusive_group()
    actions.add_argument('--check', action='store_true', help='只檢查 data.js 引用的分類圖')
    actions.add_argument('--split-img', action='store_true',
                         help='依現有 data.js 從 img/381、img/r 複製到 items／skills／status（不解包）')
    actions.add_argument('--import-assets', action='store_true',
                         help='掃天堂R主程式地圖、畫縮圖，並從 _PakOutput 複製 UI／立繪 PNG 寫進 data.js')
    actions.add_argument('--drive-index', metavar='清單.json',
                         help='讀取 rclone lsjson 清單，建立 Drive 圖片路徑與 ID 對照')
    actions.add_argument('--drive-maps', metavar='資料夾ID',
                         help='列出公開 Drive 地圖根資料夾（R版地圖／381版地圖）的檔案 ID，寫成 map-drive-ids.js')
    actions.add_argument('--map-thumbs', action='store_true',
                         help='把 img/maps 的地圖縮圖轉成小 WebP 到 map-thumbs/（進 git，線上列表用；需要 Pillow）')
    actions.add_argument('--serve', metavar='埠', type=int, nargs='?', const=8765,
                         help='本機開網站並轉送 /api/drive（地圖檢視器測試用，預設埠 8765）')
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
    if args.split_img:
        sys.exit(split_existing(OUT))
    if args.import_assets:
        sys.exit(import_assets(OUT))
    if args.drive_index:
        sys.exit(import_drive_index(OUT, args.drive_index))
    if args.drive_maps:
        sys.exit(import_drive_maps(OUT, args.drive_maps))
    if args.map_thumbs:
        sys.exit(export_map_thumbs(OUT))
    if args.serve:
        sys.exit(serve(args.serve))
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
