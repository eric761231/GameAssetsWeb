// Vercel 函式：替地圖檢視器轉送 Google Drive 檔案。
//
// 為什麼要轉送：Drive 的下載網址（drive.usercontent.google.com）對瀏覽器跨站的 fetch 一律回 403
// （依 Sec-Fetch-Site／Sec-Fetch-Mode 判斷，2026-11-10 實測），伺服器端讀取則正常，也支援 Range。
//
// GET /api/drive?id=<Drive 檔案 ID>[&start=<位移>&len=<長度>]
//   - 只轉送 icon-compare/map-drive-ids.js 列出的檔案（避免變成任意 Drive 檔案的公開代理）。
//   - Tile.pak 一定要帶 start／len，一次最多 4MB（Vercel 函式回應上限 4.5MB）。
//   - 範圍放在網址上，讓 Vercel CDN 能依網址快取，同一段不用每次都向 Drive 讀。
// 本機測試用 build.py --serve，規則相同。

const fs = require('fs');
const path = require('path');

const MAX_LEN = 4 * 1024 * 1024;
let allow = null;

function loadAllow() {
    if (allow) {
        return allow;
    }
    const file = path.join(process.cwd(), 'icon-compare', 'map-drive-ids.js');
    const text = fs.readFileSync(file, 'utf8');
    const json = JSON.parse(text.slice(text.indexOf('{'), text.lastIndexOf('}') + 1));
    const files = new Set();
    const paks = new Set();
    for (const key of Object.keys(json)) {
        const version = json[key];
        if (!version || !version.maps) {
            continue;
        }
        files.add(version.tileIdx);
        paks.add(version.tilePak);
        for (const mapId of Object.keys(version.maps)) {
            const blocks = version.maps[mapId];
            for (const name of Object.keys(blocks)) {
                files.add(blocks[name]);
            }
        }
    }
    allow = { files: files, paks: paks };
    return allow;
}

function fail(res, status, message) {
    res.statusCode = status;
    res.setHeader('Content-Type', 'text/plain; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.end(message);
}

module.exports = async function handler(req, res) {
    const url = new URL(req.url, 'http://localhost');
    const id = url.searchParams.get('id') || '';
    const hasRange = url.searchParams.has('start');
    const start = parseInt(url.searchParams.get('start') || '0', 10);
    const len = parseInt(url.searchParams.get('len') || '0', 10);
    let list;
    try {
        list = loadAllow();
    } catch (e) {
        fail(res, 500, 'map-drive-ids.js 讀取失敗');
        return;
    }
    const isPak = list.paks.has(id);
    if (!isPak && !list.files.has(id)) {
        fail(res, 404, '不在地圖檔案清單內');
        return;
    }
    if (hasRange) {
        if (!(start >= 0) || !(len > 0) || len > MAX_LEN) {
            fail(res, 400, '範圍不合理');
            return;
        }
    } else if (isPak) {
        fail(res, 400, 'Tile.pak 必須指定範圍');
        return;
    }
    const upstreamUrl = 'https://drive.usercontent.google.com/download?id=' + encodeURIComponent(id) +
        '&export=download&confirm=t';
    const headers = {};
    if (hasRange) {
        headers.Range = 'bytes=' + start + '-' + (start + len - 1);
    }
    let upstream;
    try {
        upstream = await fetch(upstreamUrl, { headers: headers, redirect: 'follow' });
    } catch (e) {
        fail(res, 502, 'Drive 連線失敗');
        return;
    }
    if (hasRange ? upstream.status !== 206 : upstream.status !== 200) {
        try {
            await upstream.body.cancel();
        } catch (e) {
        }
        fail(res, 502, 'Drive 回應 ' + upstream.status);
        return;
    }
    const type = upstream.headers.get('content-type') || '';
    if (type.indexOf('text/html') >= 0) {
        // 流量超過時 Drive 會回 HTML 說明頁
        fail(res, 502, 'Drive 回傳網頁（可能是下載次數超過上限）');
        return;
    }
    const body = Buffer.from(await upstream.arrayBuffer());
    if (hasRange && body.length !== len) {
        fail(res, 502, '分段長度不符');
        return;
    }
    res.statusCode = 200;
    res.setHeader('Content-Type', 'application/octet-stream');
    res.setHeader('Content-Length', String(body.length));
    res.setHeader('Cache-Control', 'public, max-age=86400, s-maxage=31536000, immutable');
    res.end(body);
};
