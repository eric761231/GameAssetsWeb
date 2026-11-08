# -*- coding: utf-8 -*-
"""381 ↔ R 圖示「形狀比對」（2026-11-06）。可單獨執行，輸出 icon-compare/shape-data.js 給 shape-compare.html。

用法（在 GameAssets 根目錄）：
    python icon-compare/_build/shapematch.py              全部分類（狀態、道具、技能）比對並輸出 shape-data.js
    python icon-compare/_build/shapematch.py --cats status 只比對狀態圖示（status,items,skills 逗號分隔）
    python icon-compare/_build/shapematch.py --validate   只印驗證組（已知同圖／不同圖）分數與隨機不同圖分佈，不輸出
    python icon-compare/_build/shapematch.py --check      只檢查 shape-data.js 引用的圖是否都在
    python icon-compare/_build/shapematch.py --query 381:5272,381:5220 [--top 10] [--sheet <out.png>]
                                                      單張查詢：指定圖對另一邊全部候選池的前 N 名分數（不輸出 shape-data.js）；
                                                      --sheet 另存對照圖（每列：查詢圖、前 N 名各附圖號與分數，放大 2 倍）
    python icon-compare/_build/shapematch.py --query 381:5272 --with r:1104   只印指定兩張的分數（--with 逗號分隔多張）
    python icon-compare/_build/shapematch.py --export-status-map <xml>  讀現有 shape-data.js，輸出狀態圖示例外表
                                                                      （GameLauncher EditXml/RStatusIconMap.xml，規則見 export_status_map 上方註解）

需要 Python 3 + Pillow + numpy（pip install pillow numpy）。只讀 img/381、img/r 與 data.js，不改它們、不複製圖。

輸入：
- data.js（build.py 產生）：取各分類（items／skills／status）381 側與 R 側用到的圖號與名稱。
- 381 候選池：img/381/<數字>.png（只收純數字檔名；<數字>c.png 等其他檔不收）。
- R 候選池：img/r/<數字>.png（排除 <數字>_d.png 灰圖、Thumbs.db、list 等非建置產生的檔）。

演算法（以形狀為主，顏色只佔 10%）：
1. 取前景強度圖 ink（0～1）：
   - 先裁掉外圍全透明的邊。有透明背景的圖（裁完後 alpha < 0.04 的全透明像素超過 8%）：ink = alpha ×（0.3 + 0.7 × 亮度），暗色道具也保留輪廓。
   - 不透明的圖（381 狀態／技能圖、R 狀態圖都有方框底；R 狀態圖框內是半透明暗底，先乘 alpha 疊到黑底）：四邊各切掉 12% 去掉外框，
     取切完後最外 2 像素一圈的 RGB 中位數當底色，ink = 與底色的 RGB 距離
     （遮罩超過 60% 表示有大片發光底或圖案碰到切邊，在遮罩內再做一次 Otsu 取較亮的那一層）。
     這樣亮底暗紋、暗底亮紋都算同一個圖案（不受明暗反相影響）；381 狀態圖原生用濾色貼圖，黑底＝不畫，也符合。
   - ink 除以第 99 百分位數再夾到 0～1。
2. 前景遮罩 = ink > max(Otsu 門檻, 0.15)；裁到遮罩的外框，補成正方形（置中、保持長寬比），縮到 32×32。
3. 形狀特徵（全部在 32×32 上）：
   - 遮罩容錯重疊 F（權重 0.35）：P = |A ∩ 膨脹(B)| / |A|，R = |膨脹(A) ∩ B| / |B|，F = 2PR/(P+R)（膨脹 3×3，容許 1 像素偏移）。
   - 邊緣相關 E（0.45）：ink 的 Sobel 梯度強度，模糊 [1,2,1] 兩次後的皮爾森相關係數（負值當 0）。
   - pHash H（0.10）：ink 做 32×32 DCT，取左上 8×8 去掉直流共 63 個係數，大於中位數為 1；
     相似度 = 1 − 2×漢明距離/63（隨機約 0），負值當 0。
   - 顏色 C（0.10，輔助）：前景像素的色相 12 格（依飽和度加權）＋低飽和暗／中／亮 3 格，Bhattacharyya 係數。
   分數 = 100 ×（0.35F + 0.45E + 0.10H + 0.10C），0～100。
   權重由驗證組（狀態圖同圖 25 組 vs 不同圖 12 組＋狀態圖互比 142 組）的 AUC 格點搜尋挑出：
   E 最能分辨，F 對不同圖也常有 0.7～0.9（容錯太寬）只能當輔助，pHash 對 381／R 的發光畫法差異敏感、權重壓低。
4. 全部用矩陣乘法向量化（每種特徵都化成內積），分塊計算，每列留前 3 名。
門檻（SCORE_SAME、TIE）由 --validate 的驗證組與隨機不同圖分佈決定，見 README「形狀比對」。
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
IC = os.path.dirname(HERE)

S = 32
W_MASK, W_EDGE, W_HASH, W_COLOR = 0.35, 0.45, 0.10, 0.10
SCORE_SAME = 70   # ≥ 這個分數視為「應是同一張圖」；第一名 < 這個分數視為「另一邊可能沒有對應圖」（驗證見 README）
TIE = 2.0         # 同圖號分數與第一名差距在這以內算並列第一（R 常把同一個圖案存成多個圖號、只差顏色或細節）
TOPK = 3

# 驗證組（2026-11-06 肉眼比對 img/381 與 img/r 的對照圖）：
# VALID_SAME：狀態圖同圖號、新舊版同一個圖案（加速 5397、勇水 5395、緩速 480、六芒星 5399 …）→ 決定 SCORE_SAME。
VALID_SAME = [
    (5397, 5397), (5395, 5395), (480, 480), (485, 485), (783, 783), (482, 482), (483, 483),
    (552, 552), (813, 813), (804, 804), (807, 807), (738, 738), (771, 771), (827, 827),
    (5241, 5241), (890, 890), (1101, 1101), (1125, 1125), (1119, 1119), (1558, 1558),
    (1559, 1559), (1110, 1110), (5399, 5399), (1477, 1477), (1561, 1561),
]
# VALID_REDRAWN：同一個東西但 R 重畫（道具、技能；構圖、比例不同），只列出分數供參考，不拿來定門檻。
VALID_REDRAWN = [
    (1, 1), (309, 309), (962, 962), (1062, 1062), (1202, 1202), (1314, 1314), (1512, 1512),
    (383, 383), (396, 396), (409, 409), (424, 424), (655, 655), (743, 743), (784, 784),
]
# VALID_DIFF：圖案不同（735→5732 小十字對劍、880 三叉對插地的劍、1609→9836 十字對徽章、1122 爆裂對交叉劍）
# ＋不同狀態圖互比 → 應低於 SCORE_SAME。
VALID_DIFF = [
    (735, 5732), (880, 880), (1609, 9836), (5397, 480), (5395, 485), (1, 5397), (482, 1),
    (552, 813), (309, 1202), (5399, 1477), (1558, 1110), (783, 890),
]


def load_data_js(path):
    t = open(path, encoding='utf-8').read()
    return json.loads(t[t.index('{'):t.rindex('}') + 1])


def _icon(side):
    if not side:
        return 0
    return int(side.get('icon') or side.get('iconNo') or 0)


def _name(side):
    if not side:
        return ''
    if side.get('names'):
        return side['names'][0]
    if side.get('name'):
        return side['name']
    if side.get('effects'):
        return side['effects'][0].get('name', '')
    return ''


def category_icons(data, cats):
    """回傳 (a, b)：a[圖號] = {'cats': set, 'name': str}（381 側），b 同（R 側）。"""
    a = {}
    b = {}
    for cat in cats:
        for row in data.get(cat, []):
            for key, out in (('a', a), ('b', b)):
                side = row.get(key)
                n = _icon(side)
                if n <= 0:
                    continue
                ent = out.setdefault(n, {'cats': set(), 'name': ''})
                ent['cats'].add(cat)
                if not ent['name']:
                    ent['name'] = _name(side)
    return a, b


def pool(folder, exclude_suffix=True):
    out = []
    for f in os.listdir(folder):
        m = re.fullmatch(r'(\d+)\.png', f)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


# ---------- 特徵 ----------

def _otsu(v):
    import numpy as np
    hist, edges = np.histogram(v, bins=64, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 0.5
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = total - w0
    m0 = np.cumsum(hist * centers)
    mt = m0[-1]
    with np.errstate(divide='ignore', invalid='ignore'):
        mu0 = m0 / w0
        mu1 = (mt - m0) / w1
        between = w0 * w1 * (mu0 - mu1) ** 2
    between[~np.isfinite(between)] = 0
    return float(centers[int(np.argmax(between))])


def _blur121(x):
    import numpy as np
    p = np.pad(x, 1, mode='edge')
    x = (p[:-2, 1:-1] + 2 * p[1:-1, 1:-1] + p[2:, 1:-1]) / 4
    p = np.pad(x, 1, mode='edge')
    return (p[1:-1, :-2] + 2 * p[1:-1, 1:-1] + p[1:-1, 2:]) / 4


def _dilate(m):
    import numpy as np
    p = np.pad(m, 1)
    out = np.zeros_like(m)
    for dy in range(3):
        for dx in range(3):
            out = np.maximum(out, p[dy:dy + m.shape[0], dx:dx + m.shape[1]])
    return out


_DCT = None


def _dct_matrix(n):
    import numpy as np
    k = np.arange(n)
    d = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    d[0] *= 1 / np.sqrt(2)
    return d * np.sqrt(2 / n)


def _ink_mask(ink):
    """ink 除以第 99 百分位數夾到 0～1，遮罩 = ink > max(Otsu, 0.15)；太空回傳 None。"""
    import numpy as np
    scale = float(np.percentile(ink, 99))
    if scale < 1e-3:
        return None
    ink = np.clip(ink / scale, 0, 1)
    mask = ink > max(_otsu(ink.ravel()), 0.15)
    if mask.sum() < 3:
        return None
    return ink, mask


def features(path):
    """回傳 dict（mask、dil、edge、hash、color 各為一維 float32），讀不到或全空回傳 None。"""
    import numpy as np
    from PIL import Image
    global _DCT
    if _DCT is None:
        _DCT = _dct_matrix(S)
    try:
        im = Image.open(path).convert('RGBA')
    except Exception:
        return None
    arr = np.asarray(im, dtype=np.float32) / 255.0
    ys, xs = np.nonzero(arr[..., 3] >= 0.04)
    if len(ys) < 3:
        return None
    # 先裁掉外圍全透明的邊（381 部分狀態圖外圍有 1 像素透明框），再判斷是不是透明底的圖
    arr = arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    h, w = alpha.shape
    alpha_mode = (alpha < 0.04).mean() > 0.08
    if alpha_mode:
        ink = alpha * (0.3 + 0.7 * lum)
    else:
        rgb = rgb * alpha[..., None]
        m = int(round(0.12 * min(w, h)))
        if min(w, h) - 2 * m >= 8:
            rgb = rgb[m:h - m, m:w - m]
            alpha = alpha[m:h - m, m:w - m]
            h, w = alpha.shape
        ring = np.concatenate([rgb[:2].reshape(-1, 3), rgb[-2:].reshape(-1, 3),
                               rgb[:, :2].reshape(-1, 3), rgb[:, -2:].reshape(-1, 3)])
        cand = _ink_mask(np.sqrt(((rgb - np.median(ring, axis=0)) ** 2).sum(axis=2)) / np.sqrt(3))
        if cand is not None and cand[1].mean() > 0.6:
            # 圖案大到碰到切邊、或有大片發光底（例：381 六芒星 5399）→ 在遮罩內再做一次 Otsu，取較亮的那一層
            ink0, m0 = cand
            t2 = _otsu(ink0[m0])
            m2 = ink0 > t2
            if m2.sum() >= 3:
                cand = (ink0, m2)
        if cand is None:
            return None
        ink, mask = cand
    if alpha_mode:
        cand = _ink_mask(ink)
        if cand is None:
            return None
        ink, mask = cand
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    ink_c = ink[y0:y1, x0:x1]
    msk_c = mask[y0:y1, x0:x1].astype(np.float32)
    rgb_c = rgb[y0:y1, x0:x1]
    ch, cw = ink_c.shape
    side = max(ch, cw)
    py, px = (side - ch) // 2, (side - cw) // 2
    sq_ink = np.zeros((side, side), np.float32)
    sq_msk = np.zeros((side, side), np.float32)
    sq_ink[py:py + ch, px:px + cw] = ink_c
    sq_msk[py:py + ch, px:px + cw] = msk_c
    I = np.asarray(Image.fromarray(sq_ink).resize((S, S), Image.BILINEAR if side < S else Image.BOX), np.float32)
    M = np.asarray(Image.fromarray(sq_msk).resize((S, S), Image.BILINEAR if side < S else Image.BOX), np.float32) > 0.5
    M = M.astype(np.float32)
    if M.sum() < 1:
        M[S // 2, S // 2] = 1
    dil = _dilate(M)
    # 邊緣
    p = np.pad(I, 1, mode='edge')
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    e = _blur121(_blur121(np.sqrt(gx * gx + gy * gy))).ravel()
    e = e - e.mean()
    n = np.linalg.norm(e)
    if n > 0:
        e = e / n
    # pHash
    c = (_DCT @ I @ _DCT.T)[:8, :8].ravel()[1:]
    hb = np.where(c > np.median(c), 1.0, -1.0) / np.sqrt(63.0)
    # 顏色
    hsv = np.asarray(Image.fromarray((np.clip(rgb_c, 0, 1) * 255).astype(np.uint8)).convert('HSV'), np.float32) / 255.0
    wgt = mask[y0:y1, x0:x1].astype(np.float32)
    sat = hsv[..., 1]
    val = hsv[..., 2]
    hue_bin = np.minimum((hsv[..., 0] * 12).astype(int), 11)
    hist = np.zeros(15, np.float64)
    np.add.at(hist, hue_bin.ravel(), (wgt * sat).ravel())
    gray_bin = 12 + np.minimum((val * 3).astype(int), 2)
    np.add.at(hist, gray_bin.ravel(), (wgt * (1 - sat)).ravel())
    if hist.sum() > 0:
        hist = hist / hist.sum()
    return {
        'mask': M.ravel(), 'dil': dil.ravel(), 'edge': e.astype(np.float32),
        'hash': hb.astype(np.float32), 'color': np.sqrt(hist).astype(np.float32),
    }


def feature_matrix(folder, ids, label):
    import numpy as np
    keys = ('mask', 'dil', 'edge', 'hash', 'color')
    rows = {k: [] for k in keys}
    ok = []
    t0 = time.time()
    for i, n in enumerate(ids):
        f = features(os.path.join(folder, '%d.png' % n))
        if f is None:
            continue
        ok.append(n)
        for k in keys:
            rows[k].append(f[k])
        if (i + 1) % 2000 == 0:
            print('  %s 特徵 %d/%d（%.1fs）' % (label, i + 1, len(ids), time.time() - t0))
    mats = {k: np.stack(rows[k]).astype(np.float32) for k in keys}
    mats['area'] = mats['mask'].sum(axis=1)
    return ok, mats


def score_block(A, B, ia, ib=None):
    """A 的列 ia（切片）對 B 全部（或 ib）的分數矩陣（float32，0～100）。"""
    import numpy as np
    sel = (lambda m: m[ib]) if ib is not None else (lambda m: m)
    Am = A['mask'][ia]
    Ad = A['dil'][ia]
    Bm = sel(B['mask'])
    Bd = sel(B['dil'])
    P = (Am @ Bd.T) / A['area'][ia][:, None]
    R = (Ad @ Bm.T) / sel(B['area'])[None, :]
    F = 2 * P * R / (P + R + 1e-6)
    E = np.clip(A['edge'][ia] @ sel(B['edge']).T, 0, 1)
    H = np.clip(A['hash'][ia] @ sel(B['hash']).T, 0, 1)
    C = np.clip(A['color'][ia] @ sel(B['color']).T, 0, 1)
    return 100 * (W_MASK * F + W_EDGE * E + W_HASH * H + W_COLOR * C)


def pair_score(A, ida, B, idb, x, y):
    import numpy as np
    if x not in ida or y not in idb:
        return None
    i = ida[x]
    j = idb[y]
    return float(score_block(A, B, slice(i, i + 1), np.array([j]))[0, 0])


def match(A, ids_a, B, ids_b, rows_a, chunk=256):
    """rows_a：要算的 A 列索引。回傳每列 (top 索引, top 分數, 同圖號分數 or None)。"""
    import numpy as np
    pos_b = {n: j for j, n in enumerate(ids_b)}
    out = []
    for s in range(0, len(rows_a), chunk):
        idx = np.array(rows_a[s:s + chunk])
        sc = score_block(A, B, idx)
        top = np.argpartition(-sc, TOPK, axis=1)[:, :TOPK]
        for r in range(len(idx)):
            t = top[r][np.argsort(-sc[r, top[r]])]
            n = ids_a[idx[r]]
            same = float(sc[r, pos_b[n]]) if n in pos_b else None
            out.append((t, sc[r, t], same))
    return out


def random_diff(A, ids_a, B, ids_b, k=20000, seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    ia = rng.integers(0, len(ids_a), k)
    ib = rng.integers(0, len(ids_b), k)
    keep = np.array([ids_a[i] != ids_b[j] for i, j in zip(ia, ib)])
    ia, ib = ia[keep], ib[keep]
    vals = []
    for s in range(0, len(ia), 1000):
        a = ia[s:s + 1000]
        b = ib[s:s + 1000]
        sc = score_block(A, B, a, b)
        vals.append(np.diagonal(sc))
    return np.concatenate(vals)


def validation(A, ids_a, B, ids_b):
    import numpy as np
    ida = {n: i for i, n in enumerate(ids_a)}
    idb = {n: i for i, n in enumerate(ids_b)}
    same = [(x, y, pair_score(A, ida, B, idb, x, y)) for x, y in VALID_SAME]
    diff = [(x, y, pair_score(A, ida, B, idb, x, y)) for x, y in VALID_DIFF]
    redrawn = [(x, y, pair_score(A, ida, B, idb, x, y)) for x, y in VALID_REDRAWN]
    rnd = random_diff(A, ids_a, B, ids_b)
    sv = [s for _, _, s in same if s is not None]
    dv = [s for _, _, s in diff if s is not None]
    res = {
        'same': [[x, y, None if s is None else round(s, 1)] for x, y, s in same],
        'diff': [[x, y, None if s is None else round(s, 1)] for x, y, s in diff],
        'redrawn': [[x, y, None if s is None else round(s, 1)] for x, y, s in redrawn],
        'random': {p: round(float(np.percentile(rnd, p)), 1) for p in (50, 90, 99, 99.9)},
        'randomMax': round(float(rnd.max()), 1), 'randomN': int(len(rnd)),
        'randomOverSame': round(float((rnd >= SCORE_SAME).mean() * 100), 3),
        'sameMin': round(min(sv), 1) if sv else None,
        'sameOverThreshold': sum(1 for s in sv if s >= SCORE_SAME), 'sameN': len(sv),
        'diffOverThreshold': sum(1 for s in dv if s >= SCORE_SAME), 'diffN': len(dv),
    }
    return res


def print_validation(v):
    print('驗證：已知同圖 %d 組，≥%d 分 %d 組，最低 %s' % (v['sameN'], SCORE_SAME, v['sameOverThreshold'], v['sameMin']))
    print('  ' + ' '.join('%d→%d:%s' % (x, y, s) for x, y, s in v['same']))
    print('驗證：已知不同圖 %d 組，≥%d 分 %d 組' % (v['diffN'], SCORE_SAME, v['diffOverThreshold']))
    print('  ' + ' '.join('%d→%d:%s' % (x, y, s) for x, y, s in v['diff']))
    print('參考：同物重畫（道具／技能）' + ' '.join('%d→%d:%s' % (x, y, s) for x, y, s in v['redrawn']))
    print('隨機不同圖號 %d 組：中位數 %s、P90 %s、P99 %s、P99.9 %s、最高 %s；≥%d 分 %.3f%%' % (
        v['randomN'], v['random'][50], v['random'][90], v['random'][99], v['random'][99.9], v['randomMax'],
        SCORE_SAME, v['randomOverSame']))


def check(out_path, img):
    t = open(out_path, encoding='utf-8').read()
    d = json.loads(t[t.index('{'):t.rindex('}') + 1])
    miss = []
    total = 0
    for key, mine, other in (('f', '381', 'r'), ('r', 'r', '381')):
        for row in d.get(key, []):
            refs = [(mine, row['n'])] + [(other, c[0]) for c in row['top']]
            for side, n in refs:
                total += 1
                if not os.path.exists(os.path.join(img, side, '%d.png' % n)):
                    miss.append('%s/%d.png' % (side, n))
    print('shape-data.js 引用圖 %d 次，缺 %d 個' % (total, len(miss)))
    for m in sorted(set(miss))[:50]:
        print('  缺', m)
    return 1 if miss else 0


# 狀態圖示例外表（--export-status-map，2026-11-06 使用者同意）：GameLauncher 的 R 介面以圖號為主（381 圖號 N 畫 R 的 RIcon_N.png），
# 形狀比對找出的例外寫成 EditXml/RStatusIconMap.xml。規則（只看 381→R 的狀態分類）：
# - 改號 <map>：同圖號分數 < SCORE_SAME（或 R 沒有同號圖），第一名 ≥ MAP_MIN，且第一名明顯勝出：
#   跟「圖檔內容不同」的下一名差 ≥ TIE；或差距不到 TIE 但第一名是 R 狀態分類用到的圖、下一名不是（R 狀態圖優先）。
#   內容完全相同（同 md5）的候選視為同一張，不算第二名（例：R 752／753 同一張圖）。
# - 退回 <fallback>：沒有自動改號，且同圖號分數 < FALLBACK_BELOW → 強制畫 381 .tbt。
# - 待確認（寫成 XML 註解，不生效）：第一名 ≥ SCORE_SAME 但未達自動改號；同圖號分數在 FALLBACK_BELOW～SCORE_SAME 之間。
MAP_MIN = 85.0
FALLBACK_BELOW = 50.0
# 同號分數 < FALLBACK_BELOW 但 2026-11-06 肉眼比對 img/381 與 img/r 是同一個圖案（381 圖小、細節在 32×32 被併掉或發光底不同）：
# 不自動退回，改列待確認（建議維持畫 R 同號）。751 漩渦、824 圓環、852 散點、1116 眼睛波紋、1557 爪形火焰、3734 紋章。
STATUS_SAME_BY_EYE = {751, 824, 852, 1116, 1557, 3734}


def _file_md5(path):
    import hashlib
    try:
        with open(path, 'rb') as fp:
            return hashlib.md5(fp.read()).hexdigest()
    except OSError:
        return None


def classify_status_rows(rows, r_status, img_r):
    """回傳 (maps, fallbacks, pend_maps, pend_fallbacks)，每項是 dict（n、r、score、same、second、secondScore）。"""
    maps = []
    fallbacks = []
    pend_maps = []
    pend_fallbacks = []
    for row in rows:
        if 'status' not in row['c']:
            continue
        n = row['n']
        same = row['same']
        top = row['top']
        first_n, first_s = top[0][0], top[0][1]
        first_md5 = _file_md5(os.path.join(img_r, '%d.png' % first_n))
        second_n, second_s = None, 0.0
        for c in top[1:]:
            if first_md5 is not None and _file_md5(os.path.join(img_r, '%d.png' % c[0])) == first_md5:
                continue
            second_n, second_s = c[0], c[1]
            break
        same_low = same is None or same < SCORE_SAME
        is_auto = False
        if same_low and first_n != n and first_s >= MAP_MIN:
            margin_ok = first_s - second_s >= TIE
            status_pref = first_n in r_status and (second_n is None or second_n not in r_status)
            is_auto = margin_ok or status_pref
        ent = {'n': n, 'r': first_n, 'score': first_s, 'same': same, 'second': second_n, 'secondScore': second_s}
        if is_auto:
            maps.append(ent)
            continue
        if same_low and first_n != n and first_s >= SCORE_SAME:
            pend_maps.append(ent)
        if same is not None and same < FALLBACK_BELOW and n not in STATUS_SAME_BY_EYE:
            fallbacks.append(ent)
        elif same is not None and same < SCORE_SAME:
            pend_fallbacks.append(ent)
    return maps, fallbacks, pend_maps, pend_fallbacks


def export_status_map(shape_path, data_path, img, out_path):
    t = open(shape_path, encoding='utf-8').read()
    d = json.loads(t[t.index('{'):t.rindex('}') + 1])
    _, r_status = category_icons(load_data_js(data_path), ['status'])
    maps, fallbacks, pend_maps, pend_fallbacks = classify_status_rows(d.get('f', []), r_status, os.path.join(img, 'r'))

    def fmt(v):
        return 'none' if v is None else '%.1f' % v

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!-- R 介面狀態圖示例外對照表（GameLauncher docs/plans/status-icons-r.md 階段 8）。',
        '     由 GameAssets icon-compare/_build/shapematch.py --export-status-map 產生（形狀比對資料 %s）。' % d['meta'].get('generated', ''),
        '     map：381 圖號 icon381 改畫 R 的 RIcon_<r>.png；fallback：381 圖號 icon381 強制畫 381 的 .tbt。',
        '     規則：改號 = 同號分數 < %d（或 R 無同號圖）且第一名 >= %d、與內容不同的下一名差 >= %.0f（或第一名是 R 狀態圖、下一名不是）；' % (SCORE_SAME, MAP_MIN, TIE),
        '     退回 = 沒有改號且同號分數 < %d（肉眼確認同圖的除外，改列待確認）。待確認項目列在檔尾註解，不生效；要採用就照格式改成元素。 -->' % FALLBACK_BELOW,
        '<RStatusIconMap>',
    ]
    for e in maps:
        lines.append('  <map icon381="%d" r="%d" reason="shape %s; same %s; next %s %s"/>' % (
            e['n'], e['r'], fmt(e['score']), fmt(e['same']), e['second'], fmt(e['secondScore'])))
    for e in fallbacks:
        lines.append('  <fallback icon381="%d" reason="shape %s"/>' % (e['n'], fmt(e['same'])))
    lines.append('  <!-- 待確認：改號（第一名 >= %d 但未達自動採用；目前照同號或退回規則） -->' % SCORE_SAME)
    for e in pend_maps:
        lines.append('  <!-- pending map icon381="%d" r="%d" shape %s; same %s; next %s %s -->' % (
            e['n'], e['r'], fmt(e['score']), fmt(e['same']), e['second'], fmt(e['secondScore'])))
    lines.append('  <!-- 待確認：退回（同號分數 %d～%d，加上 < %d 但肉眼看是同圖的；目前維持畫 R 同號） -->' % (
        FALLBACK_BELOW, SCORE_SAME, FALLBACK_BELOW))
    for e in pend_fallbacks:
        eye = '; same by eye' if e['n'] in STATUS_SAME_BY_EYE else ''
        lines.append('  <!-- pending fallback icon381="%d" shape %s; best %d %s%s -->' % (
            e['n'], fmt(e['same']), e['r'], fmt(e['score']), eye))
    lines.append('</RStatusIconMap>')
    with open(out_path, 'w', encoding='utf-8', newline='\r\n') as fp:
        fp.write('\n'.join(lines) + '\n')
    print('輸出 %s：改號 %d、退回 %d、待確認改號 %d、待確認退回 %d' % (
        out_path, len(maps), len(fallbacks), len(pend_maps), len(pend_fallbacks)))
    for title, items in (('改號', maps), ('退回', fallbacks), ('待確認改號', pend_maps), ('待確認退回', pend_fallbacks)):
        print('[%s]' % title)
        for e in items:
            print('  %d → %d  第一名 %s  同號 %s  下一名 %s %s' % (
                e['n'], e['r'], fmt(e['score']), fmt(e['same']), e['second'], fmt(e['secondScore'])))
    return 0


def query(args):
    """--query side:id[,side:id…]：每張對另一邊整個候選池算分數，印前 N 名；--with 指定要另外印的對象；--sheet 輸出對照圖。"""
    import numpy as np
    from PIL import Image, ImageDraw
    qs = []
    for tok in [t for t in args.query.split(',') if t]:
        side, n = tok.split(':')
        side = side.lower()
        if side not in ('381', 'r'):
            raise SystemExit('--query 的邊只能是 381 或 r：%s' % tok)
        qs.append((side, int(n)))
    withs = []
    for tok in [t for t in (args.with_ or '').split(',') if t]:
        side, n = tok.split(':')
        withs.append((side.lower(), int(n)))
    data = load_data_js(args.data)
    meta381, metaR = category_icons(data, ['status', 'items', 'skills'])
    pools = {}
    for side in sorted({q[0] for q in qs} | {('r' if q[0] == '381' else '381') for q in qs}):
        folder = os.path.join(args.img, side)
        ids, mats = feature_matrix(folder, pool(folder), side)
        pools[side] = (ids, mats, {n: i for i, n in enumerate(ids)})
    rows = []
    for side, n in qs:
        other = 'r' if side == '381' else '381'
        ids_a, A, pos_a = pools[side]
        ids_b, B, pos_b = pools[other]
        meta_b = metaR if other == 'r' else meta381
        if n not in pos_a:
            print('%s:%d 不在候選池或讀不到特徵' % (side, n))
            continue
        sc = score_block(A, B, slice(pos_a[n], pos_a[n] + 1))[0]
        order = np.argsort(-sc)[:args.top]
        print('[%s:%d] 對 %s 候選池 %d 張，前 %d 名：' % (side, n, other, len(ids_b), args.top))
        top = []
        for k, j in enumerate(order):
            m = int(ids_b[j])
            top.append((m, float(sc[j])))
            print('  %2d. %s:%d  %.1f  %s' % (k + 1, other, m, sc[j], meta_b.get(m, {}).get('name', '')[:24]))
        if n in pos_b:
            print('  同號 %s:%d  %.1f' % (other, n, sc[pos_b[n]]))
        else:
            print('  同號 %s:%d  （%s 沒有這張圖）' % (other, n, other))
        for ws, wn in withs:
            if ws == other and wn in pos_b:
                print('  指定 %s:%d  %.1f（第 %d 名）' % (ws, wn, sc[pos_b[wn]], int((sc > sc[pos_b[wn]]).sum()) + 1))
        rows.append((side, n, other, top))
    if args.sheet and rows:
        cell, pad, lab = 64, 6, 14
        cols = args.top + 1
        sheet = Image.new('RGB', (cols * (cell + pad) + pad, len(rows) * (cell + lab + pad) + pad), (40, 40, 40))
        dr = ImageDraw.Draw(sheet)

        def paste(side, n, x, y, text):
            try:
                im = Image.open(os.path.join(args.img, side, '%d.png' % n)).convert('RGBA')
                im = im.resize((im.width * 2, im.height * 2), Image.NEAREST)
                im.thumbnail((cell, cell), Image.NEAREST)
                bg = Image.new('RGBA', im.size, (0, 0, 0, 255))
                bg.alpha_composite(im)
                sheet.paste(bg.convert('RGB'), (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
            except Exception:
                dr.text((x + 4, y + cell // 2), 'missing', fill=(255, 80, 80))
            dr.text((x, y + cell + 1), text, fill=(230, 230, 230))
        for r, (side, n, other, top) in enumerate(rows):
            y = pad + r * (cell + lab + pad)
            paste(side, n, pad, y, '%s:%d' % (side, n))
            dr.rectangle([pad - 2, y - 2, pad + cell + 1, y + cell + 1], outline=(255, 200, 0))
            for k, (m, v) in enumerate(top):
                paste(other, m, pad + (k + 1) * (cell + pad), y, '%d %.1f' % (m, v))
        os.makedirs(os.path.dirname(os.path.abspath(args.sheet)), exist_ok=True)
        sheet.save(args.sheet)
        print('對照圖 %s' % args.sheet)
    return 0


def main():
    ap = argparse.ArgumentParser(description='381 ↔ R 圖示形狀比對')
    ap.add_argument('--img', default=os.path.join(IC, 'img'))
    ap.add_argument('--data', default=os.path.join(IC, 'data.js'))
    ap.add_argument('--out', default=os.path.join(IC, 'shape-data.js'))
    ap.add_argument('--cats', default='status,items,skills')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--export-status-map', metavar='XML',
                    help='讀現有 shape-data.js（--out）與 data.js，依規則輸出狀態圖示例外表（GameLauncher RStatusIconMap.xml），不重算特徵')
    ap.add_argument('--query', metavar='SIDE:ID,...', help='單張查詢（例 381:5272,381:5220），印對另一邊整個候選池的前 N 名分數，不輸出 shape-data.js')
    ap.add_argument('--with', dest='with_', metavar='SIDE:ID,...', help='配合 --query：另外印指定圖的分數與名次（例 r:1104）')
    ap.add_argument('--top', type=int, default=10, help='配合 --query：列出前幾名（預設 10）')
    ap.add_argument('--sheet', metavar='PNG', help='配合 --query：輸出對照圖')
    args = ap.parse_args()
    if args.query:
        return query(args)
    if args.check:
        return check(args.out, args.img)
    if args.export_status_map:
        return export_status_map(args.out, args.data, args.img, args.export_status_map)
    import numpy as np
    t_all = time.time()
    cats = [c for c in args.cats.split(',') if c]
    data = load_data_js(args.data)
    cat_a, cat_b = category_icons(data, cats)
    _, cat_b_all = category_icons(data, ['status', 'items', 'skills'])
    ids381 = pool(os.path.join(args.img, '381'))
    idsR = pool(os.path.join(args.img, 'r'))
    print('候選池：381 %d 張、R %d 張（已排除 _d 灰圖與非 png）' % (len(ids381), len(idsR)))
    t0 = time.time()
    ids381, A = feature_matrix(os.path.join(args.img, '381'), ids381, '381')
    idsR, B = feature_matrix(os.path.join(args.img, 'r'), idsR, 'R')
    t_feat = time.time() - t0
    print('特徵：381 %d、R %d（%.1fs）' % (len(ids381), len(idsR), t_feat))
    v = validation(A, ids381, B, idsR)
    print_validation(v)
    if args.validate:
        return 0
    pos381 = {n: i for i, n in enumerate(ids381)}
    posR = {n: i for i, n in enumerate(idsR)}
    t0 = time.time()
    rows_a = [pos381[n] for n in sorted(cat_a) if n in pos381]
    fwd = match(A, ids381, B, idsR, rows_a)
    rows_b = [posR[n] for n in sorted(cat_b) if n in posR]
    rev = match(B, idsR, A, ids381, rows_b)
    t_match = time.time() - t0
    print('比對：381→R %d 列、R→381 %d 列（%.1fs）' % (len(rows_a), len(rows_b), t_match))

    def pack(rows, res, ids_mine, ids_other, meta_mine, meta_other):
        out = []
        for ri, (top, sc, same) in zip(rows, res):
            n = ids_mine[ri]
            ent = meta_mine[n]
            row = {
                'n': n, 'c': sorted(ent['cats']), 'nm': ent['name'][:24],
                'top': [[int(ids_other[t]), round(float(s), 1)] for t, s in zip(top, sc)],
                'same': None if same is None else round(same, 1),
            }
            row['top'] = [c + [meta_other.get(c[0], {}).get('name', '')[:16]] for c in row['top']]
            out.append(row)
        return out

    meta_a_all, _ = category_icons(data, ['status', 'items', 'skills'])
    f_rows = pack(rows_a, fwd, ids381, idsR, cat_a, cat_b_all)
    r_rows = pack(rows_b, rev, idsR, ids381, cat_b, meta_a_all)

    def stats(rows, cat):
        rs = [r for r in rows if cat in r['c']]
        has_same = [r for r in rs if r['same'] is not None]
        top1 = [r for r in has_same if r['top'][0][0] == r['n']]
        tie = [r for r in has_same if r['top'][0][0] != r['n'] and r['top'][0][1] - r['same'] <= TIE]
        in3 = [r for r in has_same if any(c[0] == r['n'] for c in r['top'])]
        renum = [r for r in rs if r['top'][0][0] != r['n'] and r['top'][0][1] >= SCORE_SAME
                 and (r['same'] is None or r['same'] < SCORE_SAME)]
        none = [r for r in rs if r['top'][0][1] < SCORE_SAME]
        same_bad = [r for r in has_same if r['same'] < SCORE_SAME]
        return {
            'rows': len(rs), 'hasSame': len(has_same), 'sameTop1': len(top1), 'sameTie': len(tie),
            'sameTop1Pct': round((len(top1) + len(tie)) * 100.0 / len(has_same), 1) if has_same else None,
            'sameTop3': len(in3), 'renum': len(renum), 'none': len(none), 'sameLow': len(same_bad),
        }
    st = {c: stats(f_rows, c) for c in cats}
    st_r = {c: stats(r_rows, c) for c in cats}
    for c in cats:
        s = st[c]
        print('[381→R %s] %d 張；有同圖號 %d，同圖號即第一 %d＋並列 %d（%s%%），在前 3 名 %d；高相似但不同圖號 %d；R 找不到相似圖 %d；同圖號但不像 %d' % (
            c, s['rows'], s['hasSame'], s['sameTop1'], s['sameTie'], s['sameTop1Pct'], s['sameTop3'], s['renum'], s['none'], s['sameLow']))
    for c in cats:
        s = st_r[c]
        print('[R→381 %s] %d 張；有同圖號 %d，同圖號即第一 %d＋並列 %d（%s%%），在前 3 名 %d；高相似但不同圖號 %d；381 找不到相似圖 %d；同圖號但不像 %d' % (
            c, s['rows'], s['hasSame'], s['sameTop1'], s['sameTie'], s['sameTop1Pct'], s['sameTop3'], s['renum'], s['none'], s['sameLow']))
    meta = {
        'generated': time.strftime('%Y-%m-%d %H:%M'), 'cats': cats,
        'weights': {'mask': W_MASK, 'edge': W_EDGE, 'hash': W_HASH, 'color': W_COLOR},
        'scoreSame': SCORE_SAME, 'tie': TIE,
        'pool381': len(ids381), 'poolR': len(idsR), 'stats': st, 'statsR': st_r, 'validation': v,
        'seconds': {'features': round(t_feat, 1), 'match': round(t_match, 1), 'total': round(time.time() - t_all, 1)},
    }
    with open(args.out, 'w', encoding='utf-8', newline='\n') as fp:
        fp.write('// 由 _build/shapematch.py 產生，不要手改\n')
        fp.write('window.SHAPE_DATA = ')
        json.dump({'meta': meta, 'f': f_rows, 'r': r_rows}, fp, ensure_ascii=False, separators=(',', ':'))
        fp.write(';\n')
    print('輸出 %s（%.1fs）' % (args.out, time.time() - t_all))
    return check(args.out, args.img)


if __name__ == '__main__':
    sys.exit(main())
