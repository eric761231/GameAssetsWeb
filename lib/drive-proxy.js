// 地圖檢視器的 Google Drive 轉送（Vercel api/drive.js 與 Netlify netlify/functions/drive.js 共用）。
//
// 為什麼要轉送：Drive 的下載網址（drive.usercontent.google.com）對瀏覽器跨站的 fetch 一律回 403
// （依 Sec-Fetch-Site／Sec-Fetch-Mode 判斷，2026-11-10 實測），伺服器端讀取則正常，也支援 Range。
//
// GET /api/drive?id=<Drive 檔案 ID>[&start=<位移>&len=<長度>]
//   - 只轉送 map-drive-allow.json 列出的檔案（build.py --drive-maps 產生），避免變成任意 Drive 檔案的公開代理。
//   - Tile.pak 一定要帶 start／len，一次最多 4MB（函式回應有大小上限）。
//   - 範圍放在網址上，CDN 才能依網址快取，同一段不用每次都向 Drive 讀。
// 本機測試用 build.py --serve，規則相同。

const allowList = require('./map-drive-allow.json');

const MAX_LEN = 4 * 1024 * 1024;
const files = new Set(allowList.files);
const paks = new Set(allowList.paks);

function text(status, message) {
    return {
        status: status,
        headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' },
        body: Buffer.from(message, 'utf8'),
    };
}

// query：URLSearchParams。回傳 { status, headers, body（Buffer） }。
async function proxyDrive(query) {
    const id = query.get('id') || '';
    const hasRange = query.has('start');
    const start = parseInt(query.get('start') || '0', 10);
    const len = parseInt(query.get('len') || '0', 10);
    const isPak = paks.has(id);
    if (!isPak && !files.has(id)) {
        return text(404, '不在地圖檔案清單內');
    }
    if (hasRange) {
        if (!(start >= 0) || !(len > 0) || len > MAX_LEN) {
            return text(400, '範圍不合理');
        }
    } else if (isPak) {
        return text(400, 'Tile.pak 必須指定範圍');
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
        return text(502, 'Drive 連線失敗');
    }
    if (hasRange ? upstream.status !== 206 : upstream.status !== 200) {
        try {
            await upstream.body.cancel();
        } catch (e) {
        }
        return text(502, 'Drive 回應 ' + upstream.status);
    }
    const type = upstream.headers.get('content-type') || '';
    if (type.indexOf('text/html') >= 0) {
        // 流量超過時 Drive 會回 HTML 說明頁
        return text(502, 'Drive 回傳網頁（可能是下載次數超過上限）');
    }
    const body = Buffer.from(await upstream.arrayBuffer());
    if (hasRange && body.length !== len) {
        return text(502, '分段長度不符');
    }
    const cache = 'public, max-age=86400, s-maxage=31536000, immutable';
    return {
        status: 200,
        headers: {
            'Content-Type': 'application/octet-stream',
            'Cache-Control': cache,
            // Netlify CDN：依網址參數分開快取、部署後保留
            'Netlify-CDN-Cache-Control': 'public, s-maxage=31536000, durable',
            'Netlify-Vary': 'query=id|start|len',
        },
        body: body,
    };
}

module.exports = { proxyDrive: proxyDrive };
