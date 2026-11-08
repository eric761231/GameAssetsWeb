# -*- coding: utf-8 -*-
"""轉職技能（1 轉 2 個、2 轉 4 個）與 R 候選圖。由 build.py 呼叫。

來源（全部唯讀）：
- 技能清單、說明：後端 src/com/lineage/server/model/skill/L1SkillId.java 的 BUFF_TRANSFOR_*（6000～6059）與上方註解。
- 名稱、castgfx：db/renewal_381.sql 的 skills。
- 381 狀態圖：後端 Skill_60xx 送 S_IconTime(castgfx)；GameLauncher 前端（src/App.cpp 讀 effectlist2）用 graphic＝castgfx 找
  effectlist2 的 icon，再讀 <icon>.tbt。effectlist2 取 client/Data.pak 的 EffectList2.xml（加密，同 xmldec）。
- 381 技能圖：client/spellicon.idx 只有 220 筆，6000 以上一律沒有。
- R 技能圖候選：SpellList-c.xml、passiveSpells-c.xml 的名稱；R 狀態圖候選：effectlist2-c.xml 的 icon，
  名稱取 startMsg 指到的 string-c.tbl 那一行（第 N+1 行，去掉 #system_xxx# 前綴，冒號前當名稱）。
- 匯出圖（build.py）：icon-compare/img/skills/381-<圖號>.png 與 R-<圖號>.png（轉職技能用技能圖資料夾）。
"""
import os
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET

CLASS_NAME = {
    'CROWN': ('王族', 'prince'),
    'KNIGHT': ('騎士', 'knight'),
    'ELF': ('妖精', 'elf'),
    'WIZARD': ('法師', 'magician'),
    'DARKELF': ('黑妖', 'darkelf'),
    'DRAGON': ('龍騎', 'dragonknight'),
    'ILLUSIONIST': ('幻術', 'illusionist'),
}
ELEMENT = {'EATRH': '地', 'WATER': '水', 'FIRE': '火', 'WIND': '風'}
ROUTE = {'A0': '1A', 'B0': '1B', 'A1': '2A1', 'A2': '2A2', 'B1': '2B1', 'B2': '2B2'}
# R 被動技能的職業欄位（xxxPos）→ R 職業
PASSIVE_CLASS = {
    'princepos': 'prince', 'knightpos': 'knight', 'elfpos': 'elf', 'magicianpos': 'magician',
    'darkelfpos': 'darkelf', 'dragonknightpos': 'dragonknight', 'illusionistpos': 'illusionist',
    'warriorpos': 'warrior', 'fencerpos': 'fencer', 'lancerpos': 'lancer', 'holyknightpos': 'holyknight',
    'fighterpos': 'fighter', 'eventpos': 'event',
}
R_CLASS_TEXT = {
    'generic': '通用', 'prince': '王族', 'knight': '騎士', 'elf': '妖精', 'magician': '法師', 'darkelf': '黑妖',
    'dragonknight': '龍騎', 'illusionist': '幻術', 'warrior': '戰士', 'fencer': '劍士', 'lancer': '槍手',
    'holyknight': '聖騎士', 'fighter': '格鬥家', 'event': '活動', 'eventspell': '活動',
}
STOP = set('之的:：·()（）[]「」 ')
MAX_CAND = 8


def norm(s):
    s = unicodedata.normalize('NFKC', re.sub(r'\\[aAfF].', '', s or ''))
    return re.sub(r'\s+', '', s).lower()


def parse_skill_ids(java_path):
    """L1SkillId.java：BUFF_TRANSFOR_* 常數與它上面的 /** */ 註解。"""
    text = open(java_path, encoding='utf-8', errors='replace').read()
    out = []
    for m in re.finditer(r'/\*\*((?:(?!\*/).)*)\*/\s*public static final int (BUFF_TRANSFOR_\w+)\s*=\s*(\d+);', text, re.S):
        doc = m.group(1)
        lines = [re.sub(r'^\s*\*\s?', '', ln).strip() for ln in doc.splitlines()]
        doc = ' '.join(ln for ln in lines if ln)
        out.append((m.group(2), int(m.group(3)), doc))
    return out


def classify(const):
    """BUFF_TRANSFOR_ELFA_EATRH0 → ('妖精', 'elf', '地', '1A')。"""
    key = const[len('BUFF_TRANSFOR_'):]
    m = re.match(r'ELF([AB])_([A-Z]+)(\d)$', key)
    if m:
        cls = CLASS_NAME['ELF']
        return cls[0], cls[1], ELEMENT.get(m.group(2), m.group(2)), ROUTE[m.group(1) + m.group(3)]
    m = re.match(r'([A-Z]+?)([AB])(\d)$', key)
    cls = CLASS_NAME[m.group(1)]
    return cls[0], cls[1], '', ROUTE[m.group(2) + m.group(3)]


def read_381_pak(idx_path, name):
    """381 .idx（uint32 筆數＋28 bytes 一筆：位移、20 bytes 檔名、長度），檔名不分大小寫。"""
    d = open(idx_path, 'rb').read()
    n = struct.unpack_from('<I', d, 0)[0]
    for i in range(n):
        p = 4 + i * 28
        if p + 28 > len(d):
            break
        off = struct.unpack_from('<I', d, p)[0]
        nm = d[p + 4:p + 24].split(b'\0')[0].decode('latin-1')
        size = struct.unpack_from('<I', d, p + 24)[0]
        if nm.lower() == name.lower():
            with open(idx_path[:-4] + '.pak', 'rb') as f:
                f.seek(off)
                return f.read(size)
    return None


def parse_effects(xml_text):
    out = []
    for m in re.finditer(r'<effect\s([^>]*?)/?>', xml_text):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        out.append(a)
    return out


def r_effect_name(strings, start_msg):
    if not start_msg:
        return '', ''
    k = int(start_msg) + 1
    if k >= len(strings):
        return '', ''
    msg = re.sub(r'^#[^#]*#', '', strings[k]).strip()
    msg = re.sub(r'\\[fF].', '', msg)
    head = re.split(r'[:：]', msg, 1)[0].strip()
    name = head if 0 < len(head) <= 14 else ''
    return name, msg


def common_keyword(a, b):
    """a、b（正規化後）最長的共同子字串，長度 ≥2，頭尾不是虛字。沒有回傳空字串。"""
    best = ''
    for i in range(len(a)):
        for j in range(i + 2, len(a) + 1):
            sub = a[i:j]
            if len(sub) <= len(best):
                continue
            if sub[0] in STOP or sub[-1] in STOP:
                continue
            if sub in b:
                best = sub
    return best


def bracket_terms(desc):
    return [t for t in re.findall(r'[\[「]([^\]」]{2,10})[\]」]', desc or '')]


def score_name(src_name, src_terms, cand_name, cand_text=''):
    """回傳 (分數, 說明)。分數 0＝不列。"""
    a = norm(src_name)
    b = norm(cand_name)
    text = norm(cand_text)
    if a and b and a == b:
        return 100, '名稱相同'
    if a and b and len(b) >= 2 and (a in b or b in a):
        return 80, '名稱包含（%s／%s）' % (src_name, cand_name)
    if a and text and a in text:
        return 70, '訊息含技能名「%s」' % src_name
    kw = common_keyword(a, b) if b else ''
    if len(kw) >= 2:
        return 40 + 5 * len(kw), '關鍵字重疊「%s」' % kw
    for t in src_terms:
        tn = norm(t)
        if tn and b and (tn == b or (len(b) >= 2 and (tn in b or b in tn))):
            return 30, '說明提到「%s」' % t
        if tn and text and len(tn) >= 2 and tn in text:
            return 20, '說明提到「%s」（在訊息裡）' % t
    return 0, ''


def build(ctx):
    """ctx：dict，含 backend_src、skills_rows（skills 表 dict 清單）、client、spell_xml、passive_xml、
    effect_xml_r（bytes）、string_r（bytes）、xml_decrypt。回傳 (轉職技能清單, 381 需要的圖號, R 需要的圖號)。"""
    consts = parse_skill_ids(os.path.join(ctx['backend_src'], 'com', 'lineage', 'server', 'model', 'skill', 'L1SkillId.java'))
    rows = {int(r['skill_id']): r for r in ctx['skills_rows']}

    # 381 effectlist2：graphic → (effect id, icon)
    raw = read_381_pak(os.path.join(ctx['client'], 'Data.idx'), 'EffectList2.xml')
    eff381 = {}
    if raw:
        for a in parse_effects(ctx['xml_decrypt'](raw).decode('utf-8', 'replace')):
            g = int(a.get('graphic') or 0)
            ic = int(a.get('icon') or 0)
            if g and ic:
                eff381[g] = (a.get('id'), ic, a.get('startMsgName', ''))

    # R 技能
    r_spells = []
    root = ET.fromstring(ctx['spell_xml'])
    for grp in root:
        for s in grp.iter('spell'):
            name = re.sub(r'\\[aAfF].', '', s.get('name') or '').strip()
            if name:
                r_spells.append({'id': 'A' + s.get('id', ''), 'name': name, 'icon': int(s.get('icon') or 0),
                                 'cls': grp.tag, 'kind': '主動'})
    for e in ET.fromstring(ctx['passive_xml']).iter('element'):
        name = re.sub(r'\\[aAfF].', '', e.get('name') or '').strip()
        cls = ''
        for k in e.attrib:
            if k.lower() in PASSIVE_CLASS:
                cls = PASSIVE_CLASS[k.lower()]
                break
        if name:
            r_spells.append({'id': 'P' + e.get('id', ''), 'name': name, 'icon': int(e.get('icon') or 0),
                             'cls': cls, 'kind': '被動'})

    # R 狀態（effectlist2-c.xml）
    strings = ctx['string_r'].decode('cp950', 'replace').split('\r\n')
    r_effects = []
    for a in parse_effects(ctx['xml_decrypt'](ctx['effect_xml_r']).decode('utf-8', 'replace')):
        ic = int(a.get('icon') or 0)
        if not ic:
            continue
        name, msg = r_effect_name(strings, a.get('startMsg'))
        r_effects.append({'id': a.get('id'), 'icon': ic, 'name': name, 'msg': msg, 'key': a.get('startMsgName', '')})

    have_r = ctx['have_r']
    result = []
    need381 = set()
    needR = set()
    for const, sid, doc in consts:
        if not (6000 <= sid <= 6059):
            continue
        cls_text, cls_key, element, route = classify(const)
        row = rows.get(sid, {})
        name = (row.get('name') or '').strip() or '（skills 表沒有這筆）'
        castgfx = int(row.get('castgfx') or 0)
        terms = bracket_terms(doc)
        st = eff381.get(castgfx)
        status381 = {'graphic': castgfx, 'icon': st[1] if st else 0, 'effect': st[0] if st else None,
                     'key': st[2] if st else ''}
        if status381['icon']:
            need381.add(status381['icon'])

        cands = []
        for r in r_spells:
            sc, how = score_name(name, terms, r['name'])
            if sc <= 0:
                continue
            if r['cls'] == cls_key:
                sc += 5
            cands.append((sc, r, how))
        cands.sort(key=lambda x: (-x[0], x[1]['id']))
        skill_c = []
        seen = set()
        for sc, r, how in cands:
            if r['icon'] in seen:
                continue
            seen.add(r['icon'])
            ok = r['icon'] in have_r
            skill_c.append({'rid': r['id'], 'name': r['name'], 'icon': r['icon'] if ok else 0, 'iconNo': r['icon'],
                            'how': how, 'cls': R_CLASS_TEXT.get(r['cls'], r['cls']) + r['kind']})
            if ok:
                needR.add(r['icon'])
            if len(skill_c) >= MAX_CAND:
                break

        # 狀態候選：自己的名稱、說明裡的詞，以及技能候選的 R 名稱（名稱相同／包含的那幾個）
        strong = [c['name'] for c in skill_c if c['how'].startswith('名稱')]
        scands = []
        for e in r_effects:
            sc, how = score_name(name, terms, e['name'], e['msg'])
            for sn in strong:
                if sn and norm(sn) in norm(e['msg']) and sc < 60:
                    sc, how = 60, '訊息含 R 技能名「%s」' % sn
            if sc <= 0:
                continue
            scands.append((sc, e, how))
        scands.sort(key=lambda x: (-x[0], int(x[1]['id'])))
        status_c = []
        seen = set()
        for sc, e, how in scands:
            if e['icon'] in seen:
                continue
            seen.add(e['icon'])
            ok = e['icon'] in have_r
            status_c.append({'eid': e['id'], 'name': e['name'] or e['key'], 'msg': e['msg'][:60],
                             'icon': e['icon'] if ok else 0, 'iconNo': e['icon'], 'how': how})
            if ok:
                needR.add(e['icon'])
            if len(status_c) >= MAX_CAND:
                break

        result.append({
            'id': sid, 'const': const, 'name': name, 'cls': cls_text, 'element': element, 'route': route,
            'stage': 1 if route.startswith('1') else 2, 'desc': doc, 'skillIcon381': 0,
            'status381': status381, 'skillC': skill_c, 'statusC': status_c,
        })
    result.sort(key=lambda x: x['id'])
    return result, need381, needR
