# -*- coding: utf-8 -*-
r"""天堂R 技能名稱 ↔ 圖示對照表（2026-11-06）。

產生 GameAssets\r-skills\r-skills.csv（UTF-8 BOM，Excel 可直接開）與 r-skills\_summary.txt（統計與缺名／缺圖清單）。
欄位與來源說明見 r-skills\README.md。只讀 R 客戶端，不改任何 pak。

重跑（在 GameAssets 根目錄）：
    python icon-compare/_build/rskills.py               輸出到 r-skills\
    python icon-compare/_build/rskills.py --out <資料夾>
    python icon-compare/_build/rskills.py --icons       另外把用到的圖解到 <輸出>\img\<圖號>.png（含 _d 停用圖）

R 客戶端路徑沿用 build.py 的 GA_RCLIENT；解包、XML 解密沿用 build.py 的 rpak_read／xml_decrypt（底層是 GameLauncher
tools/re/re_tools.py 的 rpak_entries 與 XML 金鑰）。

來源（data.idx/pak 內）：
- SpellList-c.xml：主動技能。<群組 grade=N> 底下的 <spell>；群組標籤＝職業（generic＝通用魔法，grade＝魔法等級）。
  name＝顯示名稱（繁中，可能帶 \aX 顏色碼）、icon＝圖號、xicon＝第二圖號、descid＝說明表索引（沒有時用 id）。
  子節點 <conditiontype icon=...>（條件成立時換圖）、<chargespell icon=...>（蓄力階段圖）也列出來。
- passiveSpells-c.xml：被動技能。<element>：id、xxxPos（職業＋位置）、grade、icon、name（沒有時用 desc 去掉「(a/b/c)」）。
- SpellDesc-c.tbl／spellexdesc-c.tbl（cp950，第 1 行是筆數，之後每行「索引 內容」）：主動技能的表名稱與說明。
圖：icon.idx/pak 的 <圖號>.png（不分大小寫，另有 <圖號>_d.png 停用圖）；根目錄 icon\ 散檔補查。
"""
import argparse
import csv
import os
import re
import sys
import xml.etree.ElementTree as ET

BUILD = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, BUILD)
import build  # noqa: E402  (帶入 RCLIENT、rpak_read、xml_decrypt、re_tools)

GA_ROOT = os.path.dirname(os.path.dirname(BUILD))
OUT_DEFAULT = os.path.join(GA_ROOT, 'r-skills')

CLASS_TEXT = {
    'generic': '通用', 'prince': '王族', 'knight': '騎士', 'elf': '妖精', 'magician': '法師', 'darkelf': '黑妖',
    'dragonknight': '龍騎', 'illusionist': '幻術', 'warrior': '戰士', 'fencer': '劍士', 'lancer': '槍手',
    'holyknight': '聖騎士', 'fighter': '格鬥家', 'event': '活動', 'eventspell': '活動',
}
# passiveSpells 的職業欄位（屬性名不分大小寫）
PASSIVE_POS = {
    'princepos': 'prince', 'knightpos': 'knight', 'elfpos': 'elf', 'magicianpos': 'magician',
    'darkelfpos': 'darkelf', 'dragonknightpos': 'dragonknight', 'illusionistpos': 'illusionist',
    'warriorpos': 'warrior', 'fencerpos': 'fencer', 'lancerpos': 'lancer', 'holyknightpos': 'holyknight',
    'fighterpos': 'fighter', 'eventpos': 'event',
}
# 名稱前的 \aX 顏色碼 ↔ 說明裡「等級:」的文字（由 passiveSpells-c.xml 的 descEx 對出來）
COLOR_GRADE = {'\\aL': '高級', '\\aJ': '稀有', '\\aG': '英雄', '\\aF': '傳說', '\\aH': '神話', '\\a_': '（未對出）'}

COLUMNS = ['類別', '技能ID', '名稱', '原始名稱', '名稱來源', '表名稱', '職業', '職業代碼', '群組階段', '位置',
           '等級', '顏色碼', '圖號', '圖檔', '圖存在', '停用圖', '停用圖存在', 'xicon', 'xicon圖存在',
           '母技能ID', '備註']


def strip_color(s):
    return re.sub(r'\\[aAfF].', '', s or '').strip()


def color_code(s):
    m = re.match(r'\s*(\\a.)', s or '')
    return m.group(1) if m else ''


def read_tbl(raw):
    """SpellDesc 類 .tbl：第 1 行筆數，之後「索引 內容」。回傳 {索引: 內容}。"""
    out = {}
    for ln in raw.decode('cp950', 'replace').split('\r\n')[1:]:
        m = re.match(r'(\d+) ?(.*)$', ln)
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def grade_text(desc):
    m = re.search(r'等級[:：]\s*(?:\\[af].)?\s*([^\s\^\\]+)', desc or '')
    return m.group(1) if m else ''


class IconIndex:
    def __init__(self, rclient):
        self.idx = os.path.join(rclient, 'icon.idx')
        self.pak = {e[0].lower() for e in build.re_tools.rpak_entries(self.idx)}
        loose_dir = os.path.join(rclient, 'icon')
        self.loose_dir = loose_dir
        self.loose = {n.lower() for n in os.listdir(loose_dir)} if os.path.isdir(loose_dir) else set()

    def where(self, name):
        """回傳 (位置字串, 是否存在)。icon.pak 優先列，散檔另註。"""
        key = name.lower()
        inpak = key in self.pak
        inloose = key in self.loose
        if inpak and inloose:
            return 'icon.pak:%s；icon\\%s' % (name, name), True
        if inpak:
            return 'icon.pak:%s' % name, True
        if inloose:
            return 'icon\\%s' % name, True
        return '', False


def icon_cols(icons, no):
    if not no:
        return {'圖號': 0, '圖檔': '', '圖存在': '無圖號', '停用圖': '', '停用圖存在': ''}
    where, ok = icons.where('%d.png' % no)
    dwhere, dok = icons.where('%d_d.png' % no)
    return {'圖號': no, '圖檔': where or ('%d.png' % no), '圖存在': '有' if ok else '缺',
            '停用圖': dwhere or ('%d_d.png' % no), '停用圖存在': '有' if dok else '無'}


def collect(data, icons):
    spell_xml = build.xml_decrypt(data['spelllist-c.xml'])
    passive_xml = build.xml_decrypt(data['passivespells-c.xml'])
    names = read_tbl(data['spelldesc-c.tbl'])
    exdesc = read_tbl(data['spellexdesc-c.tbl'])
    rows = []

    for grp in ET.fromstring(spell_xml):
        if grp.tag in ('catalysttype',) or not isinstance(grp.tag, str):
            continue
        cls = grp.tag
        for s in grp.iter('spell'):
            sid = int(s.get('id'))
            raw = s.get('name') or ''
            name = strip_color(raw)
            src = 'SpellList-c.xml name'
            didx = int(s.get('descid')) if s.get('descid') else sid
            tname = strip_color(names.get(didx, ''))
            if not name and tname:
                name, src = tname, 'SpellDesc-c.tbl[%d]' % didx
            notes = []
            if s.get('descid'):
                notes.append('descid=%s' % s.get('descid'))
            if tname and tname != name:
                notes.append('表名稱與 name 不同')
            if not tname:
                notes.append('SpellDesc-c.tbl[%d] 沒有' % didx)
            for k in ('displayUI', 'mythskill', 'major', 'passive', 'desc'):
                if s.get(k):
                    notes.append('%s=%s' % (k, s.get(k)))
            icon = int(s.get('icon') or 0)
            xicon = int(s.get('xicon') or 0)
            code = color_code(raw)
            grade = grade_text(exdesc.get(didx, '')) or (COLOR_GRADE.get(code, '') if code else '')
            row = {'類別': '主動', '技能ID': sid, '名稱': name, '原始名稱': raw, '名稱來源': src, '表名稱': tname,
                   '職業': CLASS_TEXT.get(cls, cls), '職業代碼': cls, '群組階段': grp.get('grade', ''),
                   '位置': s.get('pos', ''), '等級': grade, '顏色碼': code, 'xicon': xicon or '',
                   'xicon圖存在': ('有' if icons.where('%d.png' % xicon)[1] else '缺') if xicon else '',
                   '母技能ID': '', '備註': '；'.join(notes)}
            row.update(icon_cols(icons, icon))
            rows.append(row)
            # 子節點的換圖
            for kind, tag in (('主動-條件圖', 'conditiontype'), ('主動-蓄力圖', 'chargespell')):
                n = 0
                for c in s.findall(tag):
                    if not c.get('icon'):
                        continue
                    n += 1
                    ci = int(c.get('icon'))
                    cx = int(c.get('xicon') or 0)
                    sub = {'類別': kind, '技能ID': sid, '名稱': '%s（%s %d）' % (name, '條件' if tag == 'conditiontype' else '蓄力', n),
                           '原始名稱': raw, '名稱來源': '母技能 name', '表名稱': tname,
                           '職業': CLASS_TEXT.get(cls, cls), '職業代碼': cls, '群組階段': grp.get('grade', ''),
                           '位置': s.get('pos', ''), '等級': grade, '顏色碼': code, 'xicon': cx or '',
                           'xicon圖存在': ('有' if icons.where('%d.png' % cx)[1] else '缺') if cx else '',
                           '母技能ID': sid,
                           '備註': ('<%s id=%s>' % (tag, c.get('id'))) if c.get('id') else '<%s>' % tag}
                    sub.update(icon_cols(icons, ci))
                    rows.append(sub)

    for e in ET.fromstring(passive_xml).iter('element'):
        pid = int(e.get('id'))
        raw = e.get('name') or ''
        name = strip_color(raw)
        src = 'passiveSpells-c.xml name'
        if not name:
            raw = re.sub(r'\s*\(\s*\d+\s*/\s*\d+\s*/\s*\d+\s*\)\s*$', '', e.get('desc') or '')
            name = strip_color(raw)
            src = 'passiveSpells-c.xml desc（無 name）'
        cls = ''
        pos = ''
        for k, v in e.attrib.items():
            if k.lower() in PASSIVE_POS:
                cls = PASSIVE_POS[k.lower()]
                pos = v
                break
        notes = []
        if re.search(r'[가-힣]', name):
            notes.append('名稱是韓文（-c 版沒翻譯）')
        for k in ('targetSpellId', 'rangeExtension', 'summary'):
            if e.get(k):
                notes.append('%s=%s' % (k, e.get(k)))
        code = color_code(raw)
        grade = grade_text(e.get('descEx')) or (COLOR_GRADE.get(code, '') if code else '')
        row = {'類別': '被動', '技能ID': pid, '名稱': name, '原始名稱': e.get('name') or e.get('desc') or '',
               '名稱來源': src, '表名稱': '', '職業': CLASS_TEXT.get(cls, cls), '職業代碼': cls,
               '群組階段': e.get('grade', ''), '位置': pos, '等級': grade, '顏色碼': code, 'xicon': '',
               'xicon圖存在': '', '母技能ID': '', '備註': '；'.join(notes)}
        row.update(icon_cols(icons, int(e.get('icon') or 0)))
        rows.append(row)
    return rows


def summary(rows):
    lines = []
    main = [r for r in rows if r['類別'] in ('主動', '被動')]
    sub = [r for r in rows if r['類別'] not in ('主動', '被動')]
    for kind in ('主動', '被動'):
        rs = [r for r in main if r['類別'] == kind]
        lines.append('%s：%d 筆；有圖 %d、缺圖 %d、無圖號 %d；有停用圖 %d；名稱非 name 屬性 %d' % (
            kind, len(rs), sum(r['圖存在'] == '有' for r in rs), sum(r['圖存在'] == '缺' for r in rs),
            sum(r['圖存在'] == '無圖號' for r in rs), sum(r['停用圖存在'] == '有' for r in rs),
            sum(not r['名稱來源'].endswith('name') for r in rs)))
    lines.append('子圖（條件／蓄力）：%d 筆；有圖 %d' % (len(sub), sum(r['圖存在'] == '有' for r in sub)))
    xs = [r for r in rows if r['xicon'] != '']
    lines.append('有 xicon：%d 筆；xicon 圖缺 %d；xicon≠icon %d' % (
        len(xs), sum(r['xicon圖存在'] == '缺' for r in xs), sum(int(r['xicon']) != r['圖號'] for r in xs)))
    lines.append('')
    lines.append('== 缺圖（icon.pak 與 icon\\ 散檔都沒有）')
    for r in rows:
        if r['圖存在'] != '有':
            lines.append('%s\t%s\t%s\t圖號 %s\t%s' % (r['類別'], r['技能ID'], r['名稱'], r['圖號'], r['圖存在']))
    lines.append('')
    lines.append('== xicon 圖缺')
    for r in rows:
        if r['xicon圖存在'] == '缺':
            lines.append('%s\t%s\t%s\txicon %s' % (r['類別'], r['技能ID'], r['名稱'], r['xicon']))
    lines.append('')
    lines.append('== 名稱不是取自 name 屬性（或是韓文）')
    for r in main:
        if not r['名稱來源'].endswith('name') or '韓文' in r['備註']:
            lines.append('%s\t%s\t%s\t%s' % (r['類別'], r['技能ID'], r['名稱'], r['名稱來源']))
    lines.append('')
    lines.append('== 主動技能：SpellDesc-c.tbl 名稱與 name 不同或沒有')
    for r in main:
        if r['類別'] == '主動' and ('表名稱與' in r['備註'] or '沒有' in r['備註']):
            lines.append('%s\t%s\t表=%s' % (r['技能ID'], r['名稱'], r['表名稱'] or '（無）'))
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description='天堂R 技能名稱 ↔ 圖示對照表')
    ap.add_argument('--out', default=OUT_DEFAULT, help='輸出資料夾（預設 GameAssets\\r-skills）')
    ap.add_argument('--icons', action='store_true', help='把用到的圖（含 _d）解到 <輸出>\\img\\')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    data = build.rpak_read(os.path.join(build.RCLIENT, 'data.idx'),
                           ['SpellList-c.xml', 'passiveSpells-c.xml', 'SpellDesc-c.tbl', 'spellexdesc-c.tbl'])
    icons = IconIndex(build.RCLIENT)
    rows = collect(data, icons)
    rows.sort(key=lambda r: ({'主動': 0, '主動-條件圖': 0, '主動-蓄力圖': 0, '被動': 1}[r['類別']], r['技能ID'],
                             r['類別'] != '主動' and r['類別'] != '被動', r['名稱']))

    with open(os.path.join(args.out, 'r-skills.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    text = summary(rows)
    with open(os.path.join(args.out, '_summary.txt'), 'w', encoding='utf-8') as f:
        f.write('// 由 icon-compare/_build/rskills.py 產生，不要手改\n')
        f.write('R 客戶端：%s\n\n' % build.RCLIENT)
        f.write(text)
    print(text.split('\n\n')[0])
    print('輸出：%s' % args.out)

    if args.icons:
        img = os.path.join(args.out, 'img')
        os.makedirs(img, exist_ok=True)
        want = set()
        for r in rows:
            for col in ('圖號', 'xicon'):
                no = r[col]
                if no:
                    want.add('%d.png' % int(no))
                    want.add('%d_d.png' % int(no))
        want = {n for n in want if n in icons.pak or n in icons.loose}
        todo = [n for n in sorted(want) if not os.path.exists(os.path.join(img, n))]
        got = build.rpak_read(icons.idx, [n for n in todo if n in icons.pak]) if todo else {}
        for n in todo:
            if n in got:
                with open(os.path.join(img, n), 'wb') as f:
                    f.write(got[n])
            elif n in icons.loose:
                import shutil
                shutil.copyfile(os.path.join(icons.loose_dir, n), os.path.join(img, n))
        print('圖：需要 %d 張，這次解出 %d 張 → %s' % (len(want), len(todo), img))


if __name__ == '__main__':
    main()
