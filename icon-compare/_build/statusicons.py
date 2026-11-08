# -*- coding: utf-8 -*-
r"""狀態圖示分頁（2026-11-06）：381 EffectList2.xml 與 R effectlist2-c.xml 的狀態圖，依圖號配對。由 build.py 呼叫。

來源（全部唯讀）：
- 381：<GA_CLIENT>\Data\EffectList2.xml 散檔優先（GameLauncher App::readDataPack 同順序），沒有才讀 Data.pak；
  加密 XML 用 build.xml_decrypt 解。名稱＝startMsg 指到 Text.pak 的 string-c.tbl（DES 加密，re_tools.pak_des_decrypt）。
  圖＝Sprite*.pak 的 <icon>.tbt（381 原生 FUN_0045b340；GameLauncher App::statusIcon）。
- R：data.idx 的 effectlist2-c.xml；名稱＝startMsg 指到 data.idx 的 string-c.tbl；圖＝icon.idx 的 <icon>.png
  （LinR 016ae690 用效果列 +0x00 icon／+0x24 offIcon 當 on／off 圖，00db1b00 載 icon/<編號>.png）。
- 字串表索引：第 1 行是筆數，startMsg＝N 取第 N+2 行（list 索引 N+1），兩邊相同（docs/native-status-icon-381.md）。

配對規則（使用者 2026-11-06 改定、2026-11-07 放寬）：**以圖號為主**——381 狀態圖號 N（.tbt）與 R 狀態圖號 N（png）放同一列。
使用者觀察：狀態圖兩邊大部分是同一張圖的新舊版，用途（效果 ID、鍵名）因改版容易對不上。
- 列集合＝兩邊效果清單用到的圖號聯集；每一側只要「有效果引用」或「圖檔存在」就顯示該側
  （2026-11-07 放寬：R 沒有效果用到但 icon.idx 有同號圖時也顯示 R 側，例如 1554；381 側對稱）。
- 改號例外（2026-11-07）：讀 GameLauncher `EditXml/RStatusIconMap.xml` 的 `<map icon381="N" r="M"/>`
  （ctx['rstatus_map'] 給路徑），381 圖號 N **直接改成** R 圖號 M 再配對（效果清單的圖號在分組前改寫；
  圖檔由 build.py 把 img/381/N.png 複製成 img/381/M.png（解包快取）與 img/status/381-M.png，之後完全照一般規則，不另標記。
  被改號的原圖號若 R 有效果用到同號，該列不再顯示 381 側（圖已改號搬走了）。`<fallback>` 不影響本網站，不讀。
- 每一側列出「這個圖號用在哪些效果」（效果 ID、startMsgName、名稱），只當輔助資訊；該側無效果引用時效果列表為空。
- 兩邊都有效果引用時，若有任何一個效果兩邊 startMsgName 相同 → usage='同'，否則 usage='異'（標「用途不同」，不拆列）；
  任一側無效果引用、或該列是改號併列（用途本來就不同）時 usage 留空。
- 圖案相似度不在這裡算（使用者另開代理做形狀比對：_build/shapematch.py、shape-compare.html）。
"""
import os
import re
import struct


def read_381_text(client, name):
    """381 Text.idx／Text.pak（索引與內容 DES-ECB）讀一個檔，不分大小寫；沒有回傳 None。"""
    import re_tools
    idx = os.path.join(client, 'Text.idx')
    data = open(idx, 'rb').read()
    count = struct.unpack_from('<I', data, 0)[0]
    body = re_tools.pak_des_decrypt(data[4:])
    for i in range(count):
        p = i * 28
        if p + 28 > len(body):
            break
        off = struct.unpack_from('<I', body, p)[0]
        nm = body[p + 4:p + 24].split(b'\0', 1)[0].decode('cp950', 'replace')
        size = struct.unpack_from('<I', body, p + 24)[0]
        if nm.lower() == name.lower():
            with open(idx[:-4] + '.pak', 'rb') as f:
                f.seek(off)
                return re_tools.pak_des_decrypt(f.read(size))
    return None


def effect_name(lines, start_msg):
    """startMsg → (名稱, 完整訊息)。名稱取冒號前（≤16 字），否則訊息前 24 字。"""
    if not start_msg or not start_msg.lstrip('-').isdigit():
        return '', ''
    k = int(start_msg) + 1
    if k <= 0 or k >= len(lines):
        return '', ''
    msg = re.sub(r'^#[^#]*#', '', lines[k]).strip()
    msg = re.sub(r'\\[fFaA].', '', msg).replace('\\n', ' ').strip()
    head = re.split(r'[:：]', msg, 1)[0].strip()
    if 0 < len(head) <= 16:
        return head, msg
    return msg[:24], msg


def load_381(ctx):
    loose = os.path.join(ctx['client'], 'Data', 'EffectList2.xml')
    if os.path.isfile(loose):
        raw = open(loose, 'rb').read()
        src = 'client\\Data\\EffectList2.xml'
    else:
        raw = ctx['read_381_pak'](os.path.join(ctx['client'], 'Data.idx'), 'EffectList2.xml')
        src = 'client\\Data.pak:EffectList2.xml'
    effects = ctx['parse_effects'](ctx['xml_decrypt'](raw).decode('utf-8', 'replace'))
    text = read_381_text(ctx['client'], 'string-c.tbl')
    lines = text.decode('cp950', 'replace').split('\r\n') if text else []
    return effects, lines, src


def _effects_by_icon(effects, lines):
    out = {}
    for e in effects:
        icon = int(e.get('icon') or 0)
        if icon <= 0 or not e.get('id', '').isdigit():
            continue
        name, msg = effect_name(lines, e.get('startMsg'))
        out.setdefault(icon, []).append({
            'id': int(e['id']), 'key': e.get('startMsgName', ''), 'name': name or e.get('startMsgName', '') or '（無名稱）',
            'msg': msg[:80], 'graphic': int(e.get('graphic') or 0),
        })
    return out


def load_renum(ctx):
    """讀 ctx['rstatus_map']（GameLauncher EditXml\\RStatusIconMap.xml，天堂加密）的
    <map icon381="N" r="M"/> 改號例外，回傳 {N: M}。檔案不存在 → 空表。fallback 不影響本網站。"""
    path = ctx.get('rstatus_map')
    if not path or not os.path.isfile(path):
        return {}
    text = ctx['xml_decrypt'](open(path, 'rb').read()).decode('utf-8', 'replace')
    return {int(m.group(1)): int(m.group(2))
            for m in re.finditer(r'<map\s+icon381="(\d+)"\s+r="(\d+)"', text)}


def build(ctx, have381, haveR):
    r"""ctx：client、read_381_pak、parse_effects、xml_decrypt、effect_xml_r（bytes）、string_r（bytes）、
    rstatus_map（RStatusIconMap.xml 路徑，可省略）。
    have381／haveR：可用圖號集合（None＝先不判斷，只回傳兩邊要的圖號讓呼叫端去匯出）。
    回傳 (rows, need381, needR, meta)。need381 是 381 原圖號；needR 含改號目標圖號。"""
    eff381, lines381, src381 = load_381(ctx)
    effR = ctx['parse_effects'](ctx['xml_decrypt'](ctx['effect_xml_r']).decode('utf-8', 'replace'))
    linesR = ctx['string_r'].decode('cp950', 'replace').split('\r\n')
    A0 = _effects_by_icon(eff381, lines381)
    B = _effects_by_icon(effR, linesR)
    renum = {n: m for n, m in load_renum(ctx).items() if m != n}
    if have381 is None:
        return None, set(A0), set(B) | {renum[n] for n in A0 if n in renum}, None

    # 改號（2026-11-07 使用者定）：381 圖號直接改成 R 圖號再配對，不改其他規則、不另標記；
    # 圖檔由 build.py 把 img/381/<原圖號>.png 複製成 img/381/<新圖號>.png（解包快取）與 img/status/381-<新圖號>.png
    A = {}
    for n, fx in A0.items():
        A.setdefault(renum.get(n, n), []).extend(fx)
    applied = sorted(n for n in A0 if n in renum)

    rows = []
    for n in sorted(set(A) | set(B)):
        a = b = None
        # 每一側：有效果引用、或圖檔存在，就顯示該側（效果列表只當輔助資訊，可為空）。
        # 被改號搬走的 381 原圖號，即使圖檔還在也不再當同號 381 側顯示。
        if n in A or (n in have381 and n not in renum):
            a = {'icon': n if n in have381 else 0, 'iconNo': n,
                 'effects': A.get(n, []), 'name': A[n][0]['name'] if n in A else ''}
        if n in B or n in haveR:
            b = {'icon': n if n in haveR else 0, 'iconNo': n,
                 'effects': B.get(n, []), 'name': B[n][0]['name'] if n in B else ''}
        k = 'both' if a and b else ('381' if a else 'r')
        row = {'k': k, 'n': n, 'a': a, 'b': b, 'usage': ''}
        if a and b and a['effects'] and b['effects']:
            keys_a = {e['key'] for e in a['effects'] if e['key']}
            keys_b = {e['key'] for e in b['effects'] if e['key']}
            row['usage'] = '同' if keys_a & keys_b else '異'
        if not ((a and a['icon']) or (b and b['icon'])):
            row['k'] = 'missing'
        rows.append(row)
    both_fx = [r for r in rows if r['a'] and r['b'] and r['a']['effects'] and r['b']['effects']]
    meta = {
        'src381': src381 + ' + client\\Text.pak:string-c.tbl；圖 Sprite*.pak <icon>.tbt',
        'srcR': 'data.idx:effectlist2-c.xml + string-c.tbl；圖 icon.idx <icon>.png',
        'renumSrc': 'GameLauncher EditXml\\RStatusIconMap.xml <map>（%d 筆改號：%s）' % (
            len(applied), '、'.join('%d→%d' % (n, renum[n]) for n in applied)),
        'effects381': len(eff381), 'effectsR': len(effR), 'icons381': len(A0), 'iconsR': len(B),
        'renum': len(applied),
        'bothIcons': sum(1 for r in rows if r['a'] and r['b']),
        'usageSame': sum(1 for r in both_fx if r['usage'] == '同'),
        'usageDiff': sum(1 for r in both_fx if r['usage'] == '異'),
        'only381': sum(1 for r in rows if r['a'] and not r['b']),
        'onlyR': sum(1 for r in rows if r['b'] and not r['a']),
    }
    return rows, set(A), set(B), meta
