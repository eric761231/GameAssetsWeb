// Netlify 函式：地圖檢視器的 Google Drive 轉送。規則在 lib/drive-proxy.js（Vercel 版共用）。
// 網址 /api/drive 由根目錄 _redirects 轉到這裡（/.netlify/functions/drive）。
// 二進位內容要用 base64 回傳；4MB 一段編碼後約 5.4MB，在同步函式 6MB 上限內。
const { proxyDrive } = require('../../lib/drive-proxy.js');

exports.handler = async function (event) {
    const query = new URLSearchParams(event.rawQuery || '');
    if (!event.rawQuery && event.queryStringParameters) {
        for (const key of Object.keys(event.queryStringParameters)) {
            query.set(key, event.queryStringParameters[key]);
        }
    }
    const result = await proxyDrive(query);
    return {
        statusCode: result.status,
        headers: result.headers,
        body: result.body.toString('base64'),
        isBase64Encoded: true,
    };
};
