// Vercel 函式：地圖檢視器的 Google Drive 轉送。規則在 lib/drive-proxy.js（Netlify 版共用）。
const { proxyDrive } = require('../lib/drive-proxy.js');

module.exports = async function handler(req, res) {
    const url = new URL(req.url, 'http://localhost');
    const result = await proxyDrive(url.searchParams);
    res.statusCode = result.status;
    for (const name of Object.keys(result.headers)) {
        res.setHeader(name, result.headers[name]);
    }
    res.setHeader('Content-Length', String(result.body.length));
    res.end(result.body);
};
