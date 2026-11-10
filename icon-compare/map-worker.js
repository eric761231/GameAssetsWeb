// 地圖檢視器的背景執行緒：從 Google Drive 分段讀 Tile.pak 與 .s32，畫出一個地圖塊的畫布與多層縮小圖。
//
// 繪製規則是從 MapEditor（D:\程式碼測試區\C++\MapEditor）搬過來的，對應關係：
//   Tile.idx 目錄       PakFile::open／openRpak（381：28-byte 目錄；R：_RMS 276-byte 目錄、Brotli）
//   .til 區塊切分       TileSet::load
//   菱形圖塊與混色      drawTileBlock、mixForType、mix555、intrinsicAlphaForType
//   .s32／.seg 解析     MapBlock::loadFromBytes
//   地圖塊畫布與鄰塊    EditorWindow renderBlockToPyramid＋WorldMap::drawFloor／drawObjectsWithActors（不含角色、遮擋淡出）
//   多層縮小圖          ViewPyramid halveSurface（第 L 層＝畫布縮成 1/2^L）
// 像素用 Uint32Array 存 ImageData 的位元組順序（小端：0xAABBGGRR）。

import { BrotliDecode } from './vendor/brotli-decode.min.js';

const BLOCK_W = 3072;
const BLOCK_H = 1536;
const BLOCK_SIZE = 64;
const BLOCK_BIAS = 0x7e00;
const MAX_LEVEL = 7;
const FETCH_LIMIT = 6;
const TILE_BUDGET = 160 * 1024 * 1024; // 每個背景執行緒各一份（檢視器最多開 4 個）
const PARSED_BLOCK_LIMIT = 600;
const CACHE_NAME = 'ga-map-drive-v1';

// Drive 擋瀏覽器跨站讀取，一律經網站自己的 /api/drive 轉送（Vercel 函式 api/drive.js；本機用 build.py --serve）。
// 範圍寫在網址上，CDN 才能依網址快取。一段最多 RANGE_CHUNK（函式回應上限 4.5MB）。
const RANGE_CHUNK = 4 * 1024 * 1024;

function driveUrl(id, start, length) {
    let url = new URL('/api/drive', self.location.href).href + '?id=' + encodeURIComponent(id);
    if (start !== undefined) {
        url += '&start=' + start + '&len=' + length;
    }
    return url;
}

// ---------------------------------------------------------------------------
// 下載：同時最多 FETCH_LIMIT 個；Tile.pak 用 Range 只讀需要的一段。
// 讀過的內容存進 Cache Storage（https／localhost 才有），下次不用再向 Drive 下載。

let activeFetches = 0;
const fetchWaiters = [];

// 換地圖時主執行緒送新的 session；舊地圖還在排隊的下載與繪製直接放棄，不佔位置
let currentSession = 0;
const CANCELLED = new Error('已換地圖，取消');

async function withFetchSlot(task, session) {
    while (activeFetches >= FETCH_LIMIT) {
        await new Promise((resolve) => {
            fetchWaiters.push(resolve);
        });
    }
    if (session !== undefined && session !== currentSession) {
        const next = fetchWaiters.shift();
        if (next) {
            next();
        }
        throw CANCELLED;
    }
    activeFetches++;
    try {
        return await task();
    } finally {
        activeFetches--;
        const next = fetchWaiters.shift();
        if (next) {
            next();
        }
    }
}

let cachePromise = null;

function openCache() {
    if (!cachePromise) {
        cachePromise = (async () => {
            try {
                if (typeof caches === 'undefined') {
                    return null;
                }
                return await caches.open(CACHE_NAME);
            } catch (e) {
                return null;
            }
        })();
    }
    return cachePromise;
}

async function cachedBytes(key, download) {
    const cache = await openCache();
    const request = 'https://ga-map.invalid/' + key;
    if (cache) {
        try {
            const hit = await cache.match(request);
            if (hit) {
                return new Uint8Array(await hit.arrayBuffer());
            }
        } catch (e) {
        }
    }
    const bytes = await download();
    if (cache) {
        try {
            await cache.put(request, new Response(bytes));
        } catch (e) {
        }
    }
    return bytes;
}

async function fetchWhole(id, session) {
    return withFetchSlot(async () => {
        let lastError = null;
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                const res = await fetch(driveUrl(id));
                if (!res.ok) {
                    throw new Error('HTTP ' + res.status + ' ' + (await res.text()).slice(0, 80));
                }
                return new Uint8Array(await res.arrayBuffer());
            } catch (e) {
                lastError = e;
                await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
            }
        }
        throw new Error('Drive 下載失敗：' + (lastError && lastError.message));
    }, session);
}

async function fetchRange(id, start, length, session) {
    if (length <= 0) {
        return new Uint8Array(0);
    }
    if (length > RANGE_CHUNK) {
        const out = new Uint8Array(length);
        const parts = [];
        for (let at = 0; at < length; at += RANGE_CHUNK) {
            const size = Math.min(RANGE_CHUNK, length - at);
            parts.push(fetchRange(id, start + at, size, session).then((bytes) => {
                out.set(bytes, at);
            }));
        }
        await Promise.all(parts);
        return out;
    }
    return withFetchSlot(async () => {
        let lastError = null;
        for (let attempt = 0; attempt < 3; attempt++) {
            try {
                const res = await fetch(driveUrl(id, start, length));
                if (!res.ok) {
                    throw new Error('HTTP ' + res.status + ' ' + (await res.text()).slice(0, 80));
                }
                const bytes = new Uint8Array(await res.arrayBuffer());
                if (bytes.length !== length) {
                    throw new Error('分段長度不符');
                }
                return bytes;
            } catch (e) {
                lastError = e;
                await new Promise((r) => setTimeout(r, 800 * (attempt + 1)));
            }
        }
        throw new Error('Drive 分段讀取失敗：' + (lastError && lastError.message));
    }, session);
}

// ---------------------------------------------------------------------------
// Tile.idx／Tile.pak（PakFile）

function u32(b, p) {
    return (b[p] | (b[p + 1] << 8) | (b[p + 2] << 16) | (b[p + 3] << 24)) >>> 0;
}

function i32(b, p) {
    return b[p] | (b[p + 1] << 8) | (b[p + 2] << 16) | (b[p + 3] << 24);
}

function readCString(b, start, end) {
    let s = '';
    for (let i = start; i < end && b[i] !== 0; i++) {
        s += String.fromCharCode(b[i]);
    }
    return s;
}

function parseIndex(bytes) {
    const entries = new Map();
    if (bytes.length >= 8 && bytes[0] === 0x5f && bytes[1] === 0x52 && bytes[2] === 0x4d && bytes[3] === 0x53) {
        // R 版 "_RMS"：筆數、每筆 276 bytes（位移、長度、壓縮後長度、旗標 0／2＝Brotli、檔名）
        const count = u32(bytes, 4);
        if (count > 2000000 || bytes.length < 8 + count * 276) {
            throw new Error('R 索引數量不合理');
        }
        for (let i = 0; i < count; i++) {
            const rec = 8 + i * 276;
            const name = readCString(bytes, rec + 16, rec + 276);
            if (!name) {
                continue;
            }
            entries.set(name.toLowerCase(), {
                offset: u32(bytes, rec),
                size: u32(bytes, rec + 4),
                packed: u32(bytes, rec + 8),
                flags: u32(bytes, rec + 12),
            });
        }
    } else {
        // 381：筆數、每筆 28 bytes（位移、20 bytes 檔名、長度），未壓縮
        const count = u32(bytes, 0);
        if (count > 2000000 || bytes.length !== 4 + count * 28) {
            throw new Error('索引數量不合理');
        }
        for (let i = 0; i < count; i++) {
            const rec = 4 + i * 28;
            const name = readCString(bytes, rec + 4, rec + 24);
            if (!name) {
                continue;
            }
            entries.set(name.toLowerCase(), { offset: u32(bytes, rec), size: u32(bytes, rec + 24), packed: 0, flags: 0 });
        }
    }
    if (entries.size === 0) {
        throw new Error('索引是空的');
    }
    return entries;
}

// 每個版本（r、381）一份：索引、.til 快取。
const versions = new Map();

function versionState(name, source) {
    let state = versions.get(name);
    if (!state) {
        state = {
            name: name,
            tileIdx: source.tileIdx,
            tilePak: source.tilePak,
            index: null,
            indexPromise: null,
            tiles: new Map(),      // tileId → { blocks, bytes } 或 null（缺）
            tilePromises: new Map(),
            tileBytes: 0,
            parsed: new Map(),     // 檔名 → 解析後的地圖塊
            parsedPromises: new Map(),
        };
        versions.set(name, state);
    }
    return state;
}

function loadIndex(state) {
    if (!state.indexPromise) {
        state.indexPromise = cachedBytes(state.name + '/Tile.idx/' + state.tileIdx, () => fetchWhole(state.tileIdx))
            .then((bytes) => {
                state.index = parseIndex(bytes);
                return state.index;
            });
    }
    return state.indexPromise;
}

// TileSet::load：區塊位移表；每塊的結尾＝比它大的下一個位移（位移不一定照順序）。
function splitTil(data) {
    if (data.length < 8) {
        return null;
    }
    const count = i32(data, 0);
    if (count <= 0 || count > 65536) {
        return null;
    }
    const tableBytes = (count + 1) * 4;
    if (data.length < 4 + tableBytes) {
        return null;
    }
    const offsets = new Int32Array(count + 1);
    for (let i = 0; i <= count; i++) {
        offsets[i] = i32(data, 4 + i * 4);
    }
    const blobStart = 4 + tableBytes;
    const blobSize = data.length - blobStart;
    const endOffset = offsets[count];
    if (endOffset < 0 || endOffset > blobSize) {
        return null;
    }
    const bounds = Array.from(new Set(offsets)).sort((a, b) => a - b);
    const blocks = new Array(count);
    for (let i = 0; i < count; i++) {
        const start = offsets[i];
        let lo = 0;
        let hi = bounds.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (bounds[mid] <= start) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        if (start < 0 || lo >= bounds.length) {
            blocks[i] = null;
            continue;
        }
        const stop = bounds[lo];
        if (stop < start || stop > blobSize) {
            blocks[i] = null;
            continue;
        }
        blocks[i] = data.subarray(blobStart + start, blobStart + stop);
    }
    return blocks;
}

function touchTile(state, tileId, value) {
    state.tiles.delete(tileId);
    state.tiles.set(tileId, value);
}

function trimTiles(state, keep) {
    if (state.tileBytes <= TILE_BUDGET) {
        return;
    }
    for (const [id, value] of state.tiles) {
        if (state.tileBytes <= TILE_BUDGET * 0.85) {
            break;
        }
        if (keep.has(id)) {
            continue;
        }
        if (value) {
            state.tileBytes -= value.bytes;
        }
        state.tiles.delete(id);
    }
}

function loadTile(state, tileId, session) {
    if (state.tiles.has(tileId)) {
        const value = state.tiles.get(tileId);
        touchTile(state, tileId, value);
        return Promise.resolve(value);
    }
    if (state.tilePromises.has(tileId)) {
        return state.tilePromises.get(tileId);
    }
    const promise = (async () => {
        const index = await loadIndex(state);
        const entry = index.get(tileId + '.til');
        let value = null;
        if (entry && entry.size <= 32 * 1024 * 1024) {
            const stored = entry.flags === 2 ? entry.packed : entry.size;
            try {
                const raw = await cachedBytes(state.name + '/' + state.tilePak + '/' + tileId + '.til',
                    () => fetchRange(state.tilePak, entry.offset, stored, session));
                let data = null;
                if (entry.flags === 2) {
                    const out = BrotliDecode(new Int8Array(raw.buffer, raw.byteOffset, raw.byteLength));
                    data = new Uint8Array(out.buffer, out.byteOffset, out.byteLength);
                } else if (entry.flags === 0) {
                    data = raw;
                }
                const blocks = data ? splitTil(data) : null;
                if (blocks) {
                    value = { blocks: blocks, bytes: data.length };
                }
            } catch (e) {
                state.tilePromises.delete(tileId);
                throw e;
            }
        }
        if (value) {
            state.tileBytes += value.bytes;
        }
        state.tiles.set(tileId, value);
        state.tilePromises.delete(tileId);
        return value;
    })();
    state.tilePromises.set(tileId, promise);
    return promise;
}

// ---------------------------------------------------------------------------
// 圖塊繪製（drawTileBlock）

const LUT555 = new Uint32Array(32768);
for (let c = 0; c < 32768; c++) {
    const r = (c >> 10) & 31;
    const g = (c >> 5) & 31;
    const b = c & 31;
    LUT555[c] = (0xff000000 | (((b << 3) | (b >> 2)) << 16) | (((g << 3) | (g >> 2)) << 8) | ((r << 3) | (r >> 2))) >>> 0;
}

const MIX_COPY = 0;
const MIX_AVERAGE = 1;
const MIX_MULTIPLY = 2;
const MIX_INVERT = 3;
const MIX_SCREEN = 4;

function mixForType(type) {
    if ((type & 0x02) === 0) {
        return MIX_COPY;
    }
    // 型別 bit 0x10（霧）→ 濾色，黑底等於透明
    if ((type & 0x10) !== 0) {
        return MIX_SCREEN;
    }
    switch (type & 0xfe) {
        case 0x06:
        case 0x22:
            return MIX_AVERAGE;
        case 0x2a:
            return MIX_MULTIPLY;
        case 0x32:
            return MIX_INVERT;
        default:
            return MIX_COPY;
    }
}

function intrinsicAlpha(type, mix) {
    if (mix === MIX_COPY || mix === MIX_SCREEN) {
        return 255;
    }
    switch (type & 0xfe) {
        case 0x06:
            return 170;
        case 0x22:
            return 150;
        case 0x2a:
            return 96;
        case 0x32:
            return 72;
        default:
            return 180;
    }
}

function clamp31(v) {
    if (v < 0) {
        return 0;
    }
    if (v > 31) {
        return 31;
    }
    return v;
}

// dst：Uint32Array 畫布（寬 w、高 h）。block：.til 的一個區塊。
function drawTileBlock(dst, w, h, pixelX, pixelY, block) {
    if (!block || block.length < 2) {
        return;
    }
    const length = block.length;
    let idx = 0;
    const type = block[idx++];
    const mix = mixForType(type);
    const alpha = intrinsicAlpha(type, mix);

    function put(tx, ty, color) {
        if (color === 0) {
            return;
        }
        const px = pixelX + tx;
        const py = pixelY + ty;
        if (px < 0 || py < 0 || px >= w || py >= h) {
            return;
        }
        const at = py * w + px;
        if (mix === MIX_COPY) {
            dst[at] = LUT555[color & 0x7fff];
            return;
        }
        const pixel = dst[at];
        const dr = (pixel & 0xff) >> 3;
        const dg = ((pixel >> 8) & 0xff) >> 3;
        const db = ((pixel >> 16) & 0xff) >> 3;
        const sr = (color >> 10) & 31;
        const sg = (color >> 5) & 31;
        const sb = color & 31;
        let r = sr;
        let g = sg;
        let b = sb;
        if (mix === MIX_AVERAGE) {
            r = (sr + dr) >> 1;
            g = (sg + dg) >> 1;
            b = (sb + db) >> 1;
        } else if (mix === MIX_MULTIPLY) {
            r = (sr * dr / 32) | 0;
            g = (sg * dg / 32) | 0;
            b = (sb * db / 32) | 0;
        } else if (mix === MIX_SCREEN) {
            r = sr + dr - ((sr * dr / 32) | 0);
            g = sg + dg - ((sg * dg / 32) | 0);
            b = sb + db - ((sb * db / 32) | 0);
        } else if (mix === MIX_INVERT) {
            r = clamp31(dr + 31 - sr);
            g = clamp31(dg + 31 - sg);
            b = clamp31(db + 31 - sb);
        }
        let written = LUT555[(r << 10) | (g << 5) | b];
        if (alpha < 255) {
            const inv = 255 - alpha;
            const wr = written & 0xff;
            const wg = (written >> 8) & 0xff;
            const wb = (written >> 16) & 0xff;
            const or = (wr * alpha + (pixel & 0xff) * inv) / 255 | 0;
            const og = (wg * alpha + ((pixel >> 8) & 0xff) * inv) / 255 | 0;
            const ob = (wb * alpha + ((pixel >> 16) & 0xff) * inv) / 255 | 0;
            written = (0xff000000 | (ob << 16) | (og << 8) | or) >>> 0;
        }
        dst[at] = written;
    }

    // bit1＝0：實心菱形半格，bit0 決定靠左或靠右
    // 實心菱形一定是直接覆蓋（mixForType），這裡直接寫入，不經 put
    if ((type & 0x02) === 0) {
        const left = (type & 0x01) !== 0;
        for (let ty = 0; ty < 24 && idx < length - 1; ty++) {
            const count = ty <= 11 ? (ty + 1) * 2 : (23 - ty) * 2;
            const py = pixelY + ty;
            let px = pixelX + (left ? 0 : 24 - count);
            const rowOk = py >= 0 && py < h;
            const row = py * w;
            for (let p = 0; p < count && idx < length - 1; p++) {
                const color = block[idx] | (block[idx + 1] << 8);
                idx += 2;
                if (rowOk && color !== 0 && px >= 0 && px < w) {
                    dst[row + px] = LUT555[color & 0x7fff];
                }
                px++;
            }
        }
        return;
    }
    if (length < 5) {
        return;
    }
    const xOffset = block[idx++];
    const yOffset = block[idx++];
    idx++; // 寬度欄位，列資料本身已帶長度
    const rowCount = block[idx++];
    for (let ty = 0; ty < rowCount && idx < length; ty++) {
        let tx = xOffset;
        const segmentCount = block[idx++];
        for (let seg = 0; seg < segmentCount && idx < length - 1; seg++) {
            const skip = block[idx++];
            const count = block[idx++];
            tx += skip >> 1;
            for (let p = 0; p < count && idx < length - 1; p++) {
                const color = block[idx] | (block[idx + 1] << 8);
                idx += 2;
                put(tx, ty + yOffset, color);
                tx++;
            }
        }
    }
}

function tileBlock(state, tileId, indexId) {
    const file = state.tiles.get(tileId);
    if (!file || indexId < 0 || indexId >= file.blocks.length) {
        return null;
    }
    return file.blocks[indexId];
}

// ---------------------------------------------------------------------------
// .s32／.seg（MapBlock::loadFromBytes）

function parseBlockFile(bytes, compact) {
    let pos = 0;
    let ok = true;
    function u8() {
        if (pos >= bytes.length) {
            ok = false;
            return 0;
        }
        return bytes[pos++];
    }
    function u16() {
        const lo = u8();
        const hi = u8();
        return lo | (hi << 8);
    }
    function s32() {
        const lo = u16();
        const hi = u16();
        return lo | (hi << 16);
    }
    function packedTile(out, at) {
        if (compact) {
            const packed = u16();
            out[at] = packed & 0xff;
            out[at + 1] = packed >> 8;
            return;
        }
        out[at] = u8();
        out[at + 1] = u8() | (u8() << 8) | (u8() << 16);
    }
    const minimum = compact ? 64 * 128 * 2 + 2 : 64 * 128 * 4 + 2;
    if (bytes.length < minimum) {
        throw new Error('地圖塊太短');
    }
    // 地板：64 列 × 128 半格，每格 (indexId, tileId)
    const floor = new Int32Array(64 * 128 * 2);
    for (let i = 0; i < 64 * 128; i++) {
        packedTile(floor, i * 2);
    }
    const layer2Count = u16();
    if (layer2Count > 20000) {
        throw new Error('第二層數量不合理');
    }
    const layer2 = new Int32Array(layer2Count * 4);
    const tmp = new Int32Array(2);
    for (let i = 0; i < layer2Count; i++) {
        layer2[i * 4] = u8();
        layer2[i * 4 + 1] = u8();
        packedTile(tmp, 0);
        layer2[i * 4 + 2] = tmp[0];
        layer2[i * 4 + 3] = tmp[1];
    }
    // 地格屬性：只跳過（檢視器不畫）
    pos += compact ? 64 * 128 : 64 * 128 * 2;
    const groups = s32();
    if (!ok || groups < 0 || groups > 20000) {
        throw new Error('物件群組數量不合理');
    }
    const objects = [];
    for (let group = 0; group < groups; group++) {
        u16();
        const count = u16();
        if (count > 20000) {
            throw new Error('物件群組過大');
        }
        for (let i = 0; i < count; i++) {
            const x = u8();
            const y = u8();
            const layer = u8();
            packedTile(tmp, 0);
            if (x === 0xcd && y === 0xcd) {
                continue;
            }
            objects.push(x, y, layer, tmp[0], tmp[1]);
        }
    }
    if (!ok) {
        throw new Error('地圖塊在物件層被截斷');
    }
    return { floor: floor, layer2: layer2, objects: Int32Array.from(objects) };
}

function collectTileIds(parsed, into) {
    for (let i = 1; i < parsed.floor.length; i += 2) {
        into.add(parsed.floor[i]);
    }
    for (let i = 3; i < parsed.layer2.length; i += 4) {
        into.add(parsed.layer2[i]);
    }
    for (let i = 4; i < parsed.objects.length; i += 5) {
        into.add(parsed.objects[i]);
    }
}

// 快取鍵用 Drive 檔案 ID：不同地圖有同名的地圖塊（例如 7fff8000.s32），用檔名會拿到上一張地圖的資料
function loadParsed(state, fileName, fileId, session) {
    if (state.parsed.has(fileId)) {
        const value = state.parsed.get(fileId);
        state.parsed.delete(fileId);
        state.parsed.set(fileId, value);
        return Promise.resolve(value);
    }
    if (state.parsedPromises.has(fileId)) {
        return state.parsedPromises.get(fileId);
    }
    const promise = (async () => {
        try {
            const bytes = await cachedBytes(state.name + '/map/' + fileId, () => fetchWhole(fileId, session));
            let value = null;
            try {
                value = parseBlockFile(bytes, fileName.endsWith('.seg'));
            } catch (e) {
                value = null;
            }
            state.parsed.set(fileId, value);
            while (state.parsed.size > PARSED_BLOCK_LIMIT) {
                state.parsed.delete(state.parsed.keys().next().value);
            }
            return value;
        } finally {
            state.parsedPromises.delete(fileId);
        }
    })();
    state.parsedPromises.set(fileId, promise);
    return promise;
}

// ---------------------------------------------------------------------------
// 地圖塊畫布（renderBlockToPyramid）

function blockBegin(b) {
    return (b - BLOCK_BIAS) * BLOCK_SIZE;
}

function blockFileKey(bx, by) {
    return ((bx & 0xffff).toString(16).padStart(4, '0') + (by & 0xffff).toString(16).padStart(4, '0'));
}

// 畫布左上角的世界像素：x＝24(X＋Y)、y＝12(Y－X)－63 列
function canvasOrigin(bx, by) {
    const x = blockBegin(bx);
    const y = blockBegin(by);
    return { x: 24 * (x + y), y: 12 * (y - x) - 63 * 12 };
}

function tilePosition(x, y) {
    const half = x >> 1;
    return [24 * (x - half + y), 63 * 12 + 12 * (y - half)];
}

let scratch = null;
let target = null;

function renderCanvas(state, bx, by, neighbors) {
    if (!target) {
        target = new Uint32Array(BLOCK_W * BLOCK_H);
        scratch = new Uint32Array(BLOCK_W * BLOCK_H);
    }
    target.fill(0);
    const origin = canvasOrigin(bx, by);
    // WorldMap::orderedBlocks：依 (beginY − beginX)、再 (beginX ＋ beginY) 排
    neighbors.sort((a, b) => {
        const ay = blockBegin(a.by) - blockBegin(a.bx);
        const byy = blockBegin(b.by) - blockBegin(b.bx);
        if (ay !== byy) {
            return ay - byy;
        }
        return (blockBegin(a.bx) + blockBegin(a.by)) - (blockBegin(b.bx) + blockBegin(b.by));
    });

    // 地板：每塊先畫在自己的透明底上，再把不透明像素蓋上去（MapBlock 畫地板＋WorldMap::drawFloor 的 blitTransparent）
    for (const n of neighbors) {
        const o = canvasOrigin(n.bx, n.by);
        const ox = o.x - origin.x;
        const oy = o.y - origin.y;
        // 鄰塊自己的畫布跟這塊畫布重疊的範圍；只清、只疊這一塊
        const x0 = Math.max(0, ox);
        const y0 = Math.max(0, oy);
        const x1 = Math.min(BLOCK_W, ox + BLOCK_W);
        const y1 = Math.min(BLOCK_H, oy + BLOCK_H);
        if (x1 <= x0 || y1 <= y0) {
            continue;
        }
        let drew = false;
        const drawAt = (x, y, indexId, tileId) => {
            const p = tilePosition(x, y);
            const px = ox + p[0];
            const py = oy + p[1];
            if (px >= x1 || py >= y1 || px + 24 <= x0 || py + 24 <= y0) {
                return;
            }
            const block = tileBlock(state, tileId, indexId);
            if (!block) {
                return;
            }
            if (!drew) {
                if (x0 === 0 && y0 === 0 && x1 === BLOCK_W && y1 === BLOCK_H) {
                    scratch.fill(0);
                } else {
                    for (let y = y0; y < y1; y++) {
                        scratch.fill(0, y * BLOCK_W + x0, y * BLOCK_W + x1);
                    }
                }
                drew = true;
            }
            drawTileBlock(scratch, BLOCK_W, BLOCK_H, px, py, block);
        };
        const floor = n.parsed.floor;
        for (let y = 0; y < 64; y++) {
            for (let x = 0; x < 128; x++) {
                const i = (y * 128 + x) * 2;
                drawAt(x, y, floor[i], floor[i + 1]);
            }
        }
        const layer2 = n.parsed.layer2;
        for (let i = 0; i < layer2.length; i += 4) {
            drawAt(layer2[i], layer2[i + 1], layer2[i + 2], layer2[i + 3]);
        }
        if (!drew) {
            continue;
        }
        for (let y = y0; y < y1; y++) {
            let at = y * BLOCK_W + x0;
            for (let x = x0; x < x1; x++, at++) {
                const p = scratch[at];
                if ((p >>> 24) !== 0) {
                    target[at] = p;
                }
            }
        }
    }

    // 物件：WorldMap::drawObjectsWithActors 的場景走訪順序
    // 掃描線 y − ⌊x/2⌋ 由上到下、同掃描線半格欄 x 由左到右、圖層升冪、同圖層後登記的先畫
    const draws = [];
    let order = 0;
    for (const n of neighbors) {
        const o = canvasOrigin(n.bx, n.by);
        const ox = o.x - origin.x;
        const oy = o.y - origin.y;
        const beginX = blockBegin(n.bx);
        const beginY = blockBegin(n.by);
        const objects = n.parsed.objects;
        for (let i = 0; i < objects.length; i += 5) {
            const x = objects[i];
            const y = objects[i + 1];
            const p = tilePosition(x, y);
            const px = ox + p[0];
            const py = oy + p[1];
            const currentOrder = order++;
            if (px >= BLOCK_W || py >= BLOCK_H || px + 300 <= 0 || py + 300 <= 0) {
                continue;
            }
            const xHalf = beginX * 2 + x;
            const row = beginY + y;
            draws.push({
                scan: row - Math.floor(xHalf / 2),
                xHalf: xHalf,
                layer: objects[i + 2],
                order: currentOrder,
                px: px,
                py: py,
                indexId: objects[i + 3],
                tileId: objects[i + 4],
            });
        }
    }
    draws.sort((a, b) => {
        if (a.scan !== b.scan) {
            return a.scan - b.scan;
        }
        if (a.xHalf !== b.xHalf) {
            return a.xHalf - b.xHalf;
        }
        if (a.layer !== b.layer) {
            return a.layer - b.layer;
        }
        return b.order - a.order;
    });
    for (const d of draws) {
        const block = tileBlock(state, d.tileId, d.indexId);
        if (block) {
            drawTileBlock(target, BLOCK_W, BLOCK_H, d.px, d.py, block);
        }
    }
    return target;
}

// 畫布（只有不透明或全透明像素，等於預乘透明度）→ 各層 ImageBitmap。
// 第 L 層＝2×2 平均縮半 L 次（halveSurface）；輸出給 ImageData 時換回非預乘。
async function buildLevels(pixels, wantFull) {
    const out = {};
    // 共用的畫布在第一個 await 之前先複製：等待期間別的工作可能已經開始畫下一塊
    const copy = pixels.buffer.slice(0);
    let src = new Uint8Array(copy);
    if (wantFull) {
        out[0] = await createImageBitmap(new ImageData(new Uint8ClampedArray(copy), BLOCK_W, BLOCK_H));
    }
    let w = BLOCK_W;
    let h = BLOCK_H;
    for (let level = 1; level <= MAX_LEVEL; level++) {
        const nw = Math.max(1, w >> 1);
        const nh = Math.max(1, h >> 1);
        const dst = new Uint8Array(nw * nh * 4);
        const img = new Uint8ClampedArray(nw * nh * 4);
        for (let y = 0; y < nh; y++) {
            const y0 = Math.min(h - 1, y * 2);
            const y1 = Math.min(h - 1, y * 2 + 1);
            for (let x = 0; x < nw; x++) {
                const x0 = Math.min(w - 1, x * 2);
                const x1 = Math.min(w - 1, x * 2 + 1);
                const a = (y0 * w + x0) * 4;
                const b = (y0 * w + x1) * 4;
                const c = (y1 * w + x0) * 4;
                const d = (y1 * w + x1) * 4;
                const o = (y * nw + x) * 4;
                for (let ch = 0; ch < 4; ch++) {
                    dst[o + ch] = (src[a + ch] + src[b + ch] + src[c + ch] + src[d + ch]) >> 2;
                }
                const alpha = dst[o + 3];
                if (alpha > 0) {
                    img[o] = dst[o] * 255 / alpha;
                    img[o + 1] = dst[o + 1] * 255 / alpha;
                    img[o + 2] = dst[o + 2] * 255 / alpha;
                    img[o + 3] = alpha;
                }
            }
        }
        out[level] = await createImageBitmap(new ImageData(img, nw, nh));
        src = dst;
        w = nw;
        h = nh;
    }
    return out;
}

function checkSession(session) {
    if (session !== currentSession) {
        throw CANCELLED;
    }
}

// 共用的下載可能是舊地圖發起、被取消的；這張地圖還有效就重來一次
async function retryIfCancelled(session, task) {
    try {
        return await task();
    } catch (e) {
        if (e === CANCELLED && session === currentSession) {
            return task();
        }
        throw e;
    }
}

async function renderJob(msg) {
    const session = msg.session;
    const state = versionState(msg.version, msg.source);
    const files = msg.files;
    const neighbors = [];
    const waits = [];
    for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
            const nbx = msg.bx + dx;
            const nby = msg.by + dy;
            const key = blockFileKey(nbx, nby);
            const name = files[key + '.s32'] ? key + '.s32' : (files[key + '.seg'] ? key + '.seg' : null);
            if (!name) {
                continue;
            }
            waits.push(retryIfCancelled(session, () => loadParsed(state, name, files[name], session)).then((parsed) => {
                if (parsed) {
                    neighbors.push({ bx: nbx, by: nby, parsed: parsed });
                }
            }));
        }
    }
    await Promise.all(waits);
    checkSession(session);
    const tileIds = new Set();
    for (const n of neighbors) {
        collectTileIds(n.parsed, tileIds);
    }
    await loadIndex(state);
    const loads = [];
    for (const id of tileIds) {
        loads.push(retryIfCancelled(session, () => loadTile(state, id, session)).catch((e) => {
            if (e === CANCELLED) {
                throw e;
            }
            return null;
        }));
    }
    await Promise.all(loads);
    checkSession(session);
    const pixels = renderCanvas(state, msg.bx, msg.by, neighbors);
    const levels = await buildLevels(pixels, msg.wantFull);
    trimTiles(state, tileIds);
    return levels;
}

// 預載：開地圖時把這張地圖的地圖塊與用到的 .til 全部先下載進 Cache Storage（不解壓、不留在記憶體），
// 之後畫的時候直接從快取讀。下次再開同一張地圖也不用再向雲端下載。
const PREFETCH_PARALLEL = 6;

async function hasCached(key) {
    const cache = await openCache();
    if (!cache) {
        return false;
    }
    try {
        return !!(await cache.match('https://ga-map.invalid/' + key));
    } catch (e) {
        return false;
    }
}

async function runQueue(items, session, worker) {
    let next = 0;
    const runners = [];
    for (let i = 0; i < PREFETCH_PARALLEL; i++) {
        runners.push((async () => {
            while (next < items.length) {
                checkSession(session);
                const item = items[next++];
                try {
                    await worker(item);
                } catch (e) {
                    if (e === CANCELLED) {
                        throw e;
                    }
                }
            }
        })());
    }
    await Promise.all(runners);
}

async function prefetchJob(msg) {
    const session = msg.session;
    const state = versionState(msg.version, msg.source);
    const names = Object.keys(msg.files);
    let done = 0;
    let total = names.length;
    let fetched = 0;
    let lastPost = 0;
    const post = (force) => {
        const now = Date.now();
        if (force || now - lastPost > 400) {
            lastPost = now;
            self.postMessage({ type: 'progress', session: session, done: done, total: total, fetched: fetched });
        }
    };
    post(true);
    const index = await loadIndex(state);
    const tileIds = new Set();
    // 第一段：地圖塊（小檔），順便收集用到的圖塊
    await runQueue(names, session, async (name) => {
        const id = msg.files[name];
        if (!(await hasCached(state.name + '/map/' + id))) {
            fetched++;
        }
        const parsed = await loadParsed(state, name, id, session);
        if (parsed) {
            collectTileIds(parsed, tileIds);
        }
        done++;
        post(false);
    });
    // 第二段：圖塊（Tile.pak 分段）
    const tiles = Array.from(tileIds).filter((id) => index.has(id + '.til'));
    total = names.length + tiles.length;
    post(true);
    await runQueue(tiles, session, async (tileId) => {
        const entry = index.get(tileId + '.til');
        const key = state.name + '/' + state.tilePak + '/' + tileId + '.til';
        if (!(await hasCached(key))) {
            const stored = entry.flags === 2 ? entry.packed : entry.size;
            if (stored > 0 && stored <= 32 * 1024 * 1024) {
                fetched++;
                await cachedBytes(key, () => fetchRange(state.tilePak, entry.offset, stored, session));
            }
        }
        done++;
        post(false);
    });
    post(true);
}

self.onmessage = async (event) => {
    const msg = event.data;
    if (msg.type === 'session') {
        currentSession = msg.session;
        return;
    }
    if (msg.type === 'prefetch') {
        try {
            await prefetchJob(msg);
            self.postMessage({ type: 'prefetched', session: msg.session });
        } catch (e) {
            if (e !== CANCELLED) {
                self.postMessage({ type: 'prefetched', session: msg.session, error: String(e && e.message || e) });
            }
        }
        return;
    }
    if (msg.type !== 'render') {
        return;
    }
    try {
        const levels = await renderJob(msg);
        const transfer = Object.keys(levels).map((k) => levels[k]);
        self.postMessage({ type: 'rendered', job: msg.job, levels: levels }, transfer);
    } catch (e) {
        self.postMessage({ type: 'failed', job: msg.job, cancelled: e === CANCELLED, error: String(e && e.message || e) });
    }
};
