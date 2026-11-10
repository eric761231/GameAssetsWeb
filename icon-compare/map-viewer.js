// 地圖檢視器（主執行緒）：MapEditor「看地圖」的網頁版。
// 對應 MapEditor EditorWindow：鏡頭與縮放（cameraPixelX/Y、setZoom、zoomToFit、minZoom）、
// 依縮放挑多層縮小圖（paintPyramid／BlockPyramid::best）、畫面需要的地圖塊離中央近的先畫（queueMissing）、
// 滑鼠所在格（screenToWorld）。實際解析與繪製在 map-worker.js。
// 資料來源是 map-drive-ids.js（build.py --drive-maps 產生）列出的 Google Drive 檔案。
(function () {
    var BLOCK_W = 3072;
    var BLOCK_H = 1536;
    var BLOCK_SIZE = 64;
    var BLOCK_BIAS = 0x7e00;
    var MAX_LEVEL = 7;
    var KEEP_LEVEL = 4;              // 第 4 層以上（小圖）一直留著
    var BIG_BUDGET = 512 * 1024 * 1024;
    var PER_WORKER = 2;              // 每個背景執行緒同時兩件：一件畫、一件等下載
    var WORKER_COUNT = Math.max(1, Math.min(4, (navigator.hardwareConcurrency || 2) - 1));
    var MAX_ZOOM = 4;
    var BACKGROUND = '#14202a';

    var workers = [];
    var jobSeq = 0;
    var view = null;
    var session = 0;          // 每開一張地圖加一；背景執行緒放棄舊 session 的工作
    var prefetcher = null;    // 專門預載的背景執行緒（不跟繪製搶下載位置）

    function blockBegin(b) {
        return (b - BLOCK_BIAS) * BLOCK_SIZE;
    }

    function canvasOrigin(bx, by) {
        var x = blockBegin(bx);
        var y = blockBegin(by);
        return { x: 24 * (x + y), y: 12 * (y - x) - 63 * 12 };
    }

    // 背景執行緒池：各自有圖塊快取；下載過的內容存在共用的 Cache Storage，不會重複向 Drive 讀
    function ensureWorkers() {
        while (workers.length < WORKER_COUNT) {
            var slot = { worker: new Worker('map-worker.js', { type: 'module' }), busy: 0 };
            slot.worker.onmessage = (function (s) {
                return function (event) {
                    s.busy = Math.max(0, s.busy - 1);
                    if (view) {
                        view.onWorkerMessage(event.data);
                    } else {
                        closeLevels(event.data.levels);
                    }
                };
            }(slot));
            workers.push(slot);
        }
        if (!prefetcher) {
            prefetcher = new Worker('map-worker.js', { type: 'module' });
            prefetcher.onmessage = function (event) {
                if (view && event.data.session === session) {
                    view.onPrefetch(event.data);
                }
            };
        }
        return workers;
    }

    function broadcastSession() {
        session++;
        workers.forEach(function (s) {
            s.worker.postMessage({ type: 'session', session: session });
        });
        if (prefetcher) {
            prefetcher.postMessage({ type: 'session', session: session });
        }
    }

    function idleWorker() {
        var best = null;
        workers.forEach(function (s) {
            if (s.busy < PER_WORKER && (!best || s.busy < best.busy)) {
                best = s;
            }
        });
        return best;
    }

    function closeLevels(levels) {
        if (!levels) {
            return;
        }
        Object.keys(levels).forEach(function (k) {
            try {
                levels[k].close();
            } catch (e) {
            }
        });
    }

    function levelBytes(level) {
        return (BLOCK_W >> level) * (BLOCK_H >> level) * 4;
    }

    function View(stage, opts) {
        var self = this;
        this.stage = stage;
        this.opts = opts;
        this.source = opts.source;
        this.files = opts.files;
        this.blocks = [];
        var seen = {};
        Object.keys(this.files).forEach(function (name) {
            var stem = name.slice(0, 8);
            if (seen[stem]) {
                return;
            }
            seen[stem] = 1;
            self.blocks.push({ bx: parseInt(stem.slice(0, 4), 16), by: parseInt(stem.slice(4, 8), 16), key: stem });
        });
        this.blocks.sort(function (a, b) {
            return a.bx !== b.bx ? a.bx - b.bx : a.by - b.by;
        });
        this.cache = {};       // key → { levels: [], lastUse }
        this.bigBytes = 0;
        this.inFlight = {};    // key → job
        this.inFlightCount = 0;
        this.failed = {};      // key → 失敗時間
        this.failCount = 0;
        this.frame = 0;
        this.dirty = true;
        this.closed = false;
        this.zoom = 0.1;
        this.camX = 0;         // 鏡頭世界像素
        this.camY = 0;
        this.pointers = {};
        this.hover = null;

        this.canvas = document.createElement('canvas');
        this.canvas.className = 'map-canvas';
        stage.appendChild(this.canvas);
        this.ctx = this.canvas.getContext('2d');

        this.bounds = this.computeBounds();
        this.resize();
        this.zoomToFit();

        this.onResize = function () {
            self.resize();
            self.dirty = true;
        };
        window.addEventListener('resize', this.onResize);
        this.bindInput();
        this.loop = function () {
            if (self.closed) {
                return;
            }
            if (self.dirty) {
                self.dirty = false;
                self.paint();
            }
            requestAnimationFrame(self.loop);
        };
        requestAnimationFrame(this.loop);
    }

    View.prototype.computeBounds = function () {
        var b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
        this.blocks.forEach(function (blk) {
            var o = canvasOrigin(blk.bx, blk.by);
            b.x0 = Math.min(b.x0, o.x);
            b.y0 = Math.min(b.y0, o.y);
            b.x1 = Math.max(b.x1, o.x + BLOCK_W);
            b.y1 = Math.max(b.y1, o.y + BLOCK_H);
        });
        return b;
    };

    View.prototype.resize = function () {
        var dpr = window.devicePixelRatio || 1;
        var w = Math.max(1, this.stage.clientWidth);
        var h = Math.max(1, this.stage.clientHeight);
        this.dpr = dpr;
        this.viewW = w;
        this.viewH = h;
        this.canvas.width = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
        this.canvas.style.width = w + 'px';
        this.canvas.style.height = h + 'px';
    };

    View.prototype.fitZoom = function () {
        if (!this.blocks.length) {
            return 0.5;
        }
        var b = this.bounds;
        return Math.min(this.viewW / (b.x1 - b.x0), this.viewH / (b.y1 - b.y0)) * 0.95;
    };

    View.prototype.minZoom = function () {
        return Math.min(0.25, this.fitZoom() * 0.5);
    };

    View.prototype.zoomToFit = function () {
        var b = this.bounds;
        this.zoom = Math.max(0.0005, Math.min(MAX_ZOOM, this.fitZoom()));
        this.camX = (b.x0 + b.x1) / 2;
        this.camY = (b.y0 + b.y1) / 2;
        this.dirty = true;
        this.report();
    };

    // 改縮放；(vx, vy) 底下的地圖位置不動
    View.prototype.setZoom = function (zoom, vx, vy) {
        var z = Math.max(this.minZoom(), Math.min(MAX_ZOOM, zoom));
        var px = this.camX + (vx - this.viewW / 2) / this.zoom;
        var py = this.camY + (vy - this.viewH / 2) / this.zoom;
        this.zoom = z;
        this.camX = px - (vx - this.viewW / 2) / z;
        this.camY = py - (vy - this.viewH / 2) / z;
        this.dirty = true;
        this.report();
    };

    View.prototype.zoomBy = function (factor) {
        this.setZoom(this.zoom * factor, this.viewW / 2, this.viewH / 2);
    };

    // 以地板菱形中心 (24(X+Y)+24, 12(Y−X)+12) 反推格子
    View.prototype.screenToWorld = function (sx, sy) {
        var px = this.camX + (sx - this.viewW / 2) / this.zoom;
        var py = this.camY + (sy - this.viewH / 2) / this.zoom;
        var a = (px - 24) / 24;
        var b = (py - 12) / 12;
        return { x: Math.floor((a - b) / 2 + 0.5), y: Math.floor((a + b) / 2 + 0.5) };
    };

    View.prototype.desiredLevel = function () {
        var level = Math.floor(Math.log2(1 / this.zoom));
        return Math.max(0, Math.min(MAX_LEVEL, level));
    };

    // BlockPyramid::best：先找要的層，再找細的，再找粗的
    View.prototype.best = function (entry, level) {
        if (!entry) {
            return null;
        }
        var l;
        for (l = level; l >= 0; l--) {
            if (entry.levels[l]) {
                return { img: entry.levels[l], level: l };
            }
        }
        for (l = level + 1; l <= MAX_LEVEL; l++) {
            if (entry.levels[l]) {
                return { img: entry.levels[l], level: l };
            }
        }
        return null;
    };

    View.prototype.paint = function () {
        var ctx = this.ctx;
        var dpr = this.dpr;
        this.frame++;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.fillStyle = BACKGROUND;
        ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = this.zoom < 1 ? 'high' : 'low';
        var level = this.desiredLevel();
        var wanted = [];
        var w = BLOCK_W * this.zoom;
        var h = BLOCK_H * this.zoom;
        var now = Date.now();
        for (var i = 0; i < this.blocks.length; i++) {
            var blk = this.blocks[i];
            var o = canvasOrigin(blk.bx, blk.by);
            var sx = this.viewW / 2 + (o.x - this.camX) * this.zoom;
            var sy = this.viewH / 2 + (o.y - this.camY) * this.zoom;
            if (sx + w < 0 || sy + h < 0 || sx > this.viewW || sy > this.viewH) {
                continue;
            }
            var entry = this.cache[blk.key];
            var got = this.best(entry, level);
            if (entry) {
                entry.lastUse = this.frame;
            }
            if ((!got || got.level > level) && !this.inFlight[blk.key] &&
                !(this.failed[blk.key] && now - this.failed[blk.key] < 15000)) {
                var cx = sx + w / 2 - this.viewW / 2;
                var cy = sy + h / 2 - this.viewH / 2;
                wanted.push({ blk: blk, dist: cx * cx + cy * cy, full: level === 0 });
            }
            if (got) {
                ctx.drawImage(got.img, sx, sy, w, h);
            }
        }
        if (this.hover) {
            this.drawHover(ctx);
        }
        wanted.sort(function (a, b) {
            return a.dist - b.dist;
        });
        this.wanted = wanted;
        this.dispatch();
        this.report();
    };

    View.prototype.drawHover = function (ctx) {
        var c = this.hover;
        var px = 24 * (c.x + c.y) + 24;
        var py = 12 * (c.y - c.x) + 12;
        var sx = this.viewW / 2 + (px - this.camX) * this.zoom;
        var sy = this.viewH / 2 + (py - this.camY) * this.zoom;
        var hw = 24 * this.zoom;
        var hh = 12 * this.zoom;
        if (hw < 3) {
            return;
        }
        ctx.beginPath();
        ctx.moveTo(sx, sy - hh);
        ctx.lineTo(sx + hw, sy);
        ctx.lineTo(sx, sy + hh);
        ctx.lineTo(sx - hw, sy);
        ctx.closePath();
        ctx.strokeStyle = 'rgba(255, 230, 120, .9)';
        ctx.lineWidth = 1;
        ctx.stroke();
    };

    View.prototype.dispatch = function () {
        ensureWorkers();
        var slot;
        while (this.wanted && this.wanted.length && (slot = idleWorker())) {
            var want = this.wanted.shift();
            if (this.inFlight[want.blk.key]) {
                continue;
            }
            var job = ++jobSeq;
            this.inFlight[want.blk.key] = job;
            this.inFlightCount++;
            slot.busy++;
            slot.worker.postMessage({
                type: 'render',
                job: job,
                session: this.session,
                version: this.opts.version,
                source: { tileIdx: this.source.tileIdx, tilePak: this.source.tilePak },
                files: this.files,
                bx: want.blk.bx,
                by: want.blk.by,
                wantFull: want.full,
            });
        }
    };

    View.prototype.onWorkerMessage = function (msg) {
        var key = null;
        var self = this;
        Object.keys(this.inFlight).forEach(function (k) {
            if (self.inFlight[k] === msg.job) {
                key = k;
            }
        });
        if (key === null) {
            closeLevels(msg.levels);
            return;
        }
        delete this.inFlight[key];
        this.inFlightCount--;
        if (msg.type === 'failed') {
            this.failed[key] = Date.now();
            this.failCount++;
            this.lastError = msg.error;
        } else {
            delete this.failed[key];
            var entry = this.cache[key];
            if (!entry) {
                entry = { levels: [], lastUse: this.frame };
                this.cache[key] = entry;
            }
            Object.keys(msg.levels).forEach(function (k) {
                var l = parseInt(k, 10);
                if (entry.levels[l]) {
                    entry.levels[l].close();
                    if (l < KEEP_LEVEL) {
                        self.bigBytes -= levelBytes(l);
                    }
                }
                entry.levels[l] = msg.levels[k];
                if (l < KEEP_LEVEL) {
                    self.bigBytes += levelBytes(l);
                }
            });
            entry.lastUse = this.frame;
            this.trim();
        }
        this.dirty = true;
    };

    View.prototype.onPrefetch = function (msg) {
        if (msg.type === 'progress') {
            this.prefetch = msg;
        } else if (msg.type === 'prefetched') {
            this.prefetchDone = true;
            this.prefetchError = msg.error || '';
            this.failed = {};   // 檔案都進快取了，失敗的地圖塊馬上重畫
        }
        this.dirty = true;
    };

    // 大的層有記憶體上限，最久沒用的先丟（之後要用再重畫）
    View.prototype.trim = function () {
        if (this.bigBytes <= BIG_BUDGET) {
            return;
        }
        var self = this;
        var keys = Object.keys(this.cache).sort(function (a, b) {
            return self.cache[a].lastUse - self.cache[b].lastUse;
        });
        for (var i = 0; i < keys.length && this.bigBytes > BIG_BUDGET * 0.85; i++) {
            var entry = this.cache[keys[i]];
            if (entry.lastUse === this.frame) {
                continue;
            }
            for (var l = 0; l < KEEP_LEVEL; l++) {
                if (entry.levels[l]) {
                    entry.levels[l].close();
                    entry.levels[l] = null;
                    this.bigBytes -= levelBytes(l);
                }
            }
        }
    };

    View.prototype.report = function () {
        if (!this.opts.onStatus) {
            return;
        }
        var ready = 0;
        for (var k in this.cache) {
            if (Object.prototype.hasOwnProperty.call(this.cache, k)) {
                ready++;
            }
        }
        var text = '縮放 ' + Math.round(this.zoom * 1000) / 10 + '%｜已畫 ' + ready + '／' + this.blocks.length + ' 塊';
        if (this.inFlightCount > 0 || (this.wanted && this.wanted.length)) {
            text += '｜讀取中…';
        }
        if (this.prefetchDone) {
            text += this.prefetchError ? '｜預載失敗：' + this.prefetchError : '｜整張已存進快取';
        } else if (this.prefetch) {
            var p = this.prefetch;
            text += '｜預載 ' + p.done + '／' + p.total + ' 個檔（從雲端下載 ' + p.fetched + '）';
        } else {
            text += '｜預載準備中…';
        }
        if (this.failCount) {
            text += '｜失敗 ' + this.failCount + ' 次（' + (this.lastError || '') + '，稍後重試）';
        }
        if (this.hover) {
            text += '｜座標 ' + this.hover.x + ', ' + this.hover.y;
        }
        this.opts.onStatus(text);
    };

    View.prototype.bindInput = function () {
        var self = this;
        var c = this.canvas;
        function local(e) {
            var r = c.getBoundingClientRect();
            return { x: e.clientX - r.left, y: e.clientY - r.top };
        }
        this.handlers = {
            wheel: function (e) {
                e.preventDefault();
                var p = local(e);
                self.setZoom(self.zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), p.x, p.y);
            },
            down: function (e) {
                if (e.pointerType === 'mouse' && e.button !== 0) {
                    return;
                }
                c.setPointerCapture(e.pointerId);
                self.pointers[e.pointerId] = local(e);
                self.stage.classList.add('is-drag');
            },
            move: function (e) {
                var p = local(e);
                var ids = Object.keys(self.pointers);
                if (ids.length === 0) {
                    var cell = self.screenToWorld(p.x, p.y);
                    if (!self.hover || self.hover.x !== cell.x || self.hover.y !== cell.y) {
                        self.hover = cell;
                        self.dirty = true;
                    }
                    return;
                }
                if (!self.pointers[e.pointerId]) {
                    return;
                }
                if (ids.length === 1) {
                    var last = self.pointers[e.pointerId];
                    self.camX -= (p.x - last.x) / self.zoom;
                    self.camY -= (p.y - last.y) / self.zoom;
                    self.pointers[e.pointerId] = p;
                    self.dirty = true;
                    return;
                }
                // 兩指縮放
                var other = self.pointers[ids[0] == e.pointerId ? ids[1] : ids[0]];
                var before = self.pointers[e.pointerId];
                var d0 = Math.hypot(before.x - other.x, before.y - other.y);
                var d1 = Math.hypot(p.x - other.x, p.y - other.y);
                self.pointers[e.pointerId] = p;
                if (d0 > 0) {
                    self.setZoom(self.zoom * d1 / d0, (p.x + other.x) / 2, (p.y + other.y) / 2);
                }
            },
            up: function (e) {
                delete self.pointers[e.pointerId];
                if (!Object.keys(self.pointers).length) {
                    self.stage.classList.remove('is-drag');
                }
            },
            leave: function () {
                if (self.hover) {
                    self.hover = null;
                    self.dirty = true;
                }
            },
            dbl: function (e) {
                var p = local(e);
                self.setZoom(self.zoom * 2, p.x, p.y);
            },
        };
        c.addEventListener('wheel', this.handlers.wheel, { passive: false });
        c.addEventListener('pointerdown', this.handlers.down);
        c.addEventListener('pointermove', this.handlers.move);
        c.addEventListener('pointerup', this.handlers.up);
        c.addEventListener('pointercancel', this.handlers.up);
        c.addEventListener('pointerleave', this.handlers.leave);
        c.addEventListener('dblclick', this.handlers.dbl);
    };

    View.prototype.destroy = function () {
        this.closed = true;
        window.removeEventListener('resize', this.onResize);
        var self = this;
        Object.keys(this.cache).forEach(function (k) {
            self.cache[k].levels.forEach(function (img) {
                if (img) {
                    img.close();
                }
            });
        });
        this.cache = {};
        this.canvas.remove();
    };

    window.MapViewer = {
        // 能不能開：Worker 不能從 file:// 載入
        supported: function () {
            return location.protocol !== 'file:' && typeof Worker !== 'undefined' && typeof createImageBitmap !== 'undefined';
        },
        // opts：{ version: 'r'|'381', mapId, source: MAP_DRIVE[version], files: {檔名: ID}, onStatus }
        open: function (stage, opts) {
            this.close();
            ensureWorkers();
            broadcastSession();
            view = new View(stage, opts);
            view.session = session;
            // 預載這張地圖的所有檔案（地圖塊與用到的圖塊）進瀏覽器快取
            prefetcher.postMessage({
                type: 'prefetch',
                session: session,
                version: opts.version,
                source: { tileIdx: opts.source.tileIdx, tilePak: opts.source.tilePak },
                files: opts.files,
            });
            return view;
        },
        close: function () {
            if (view) {
                view.destroy();
                view = null;
                broadcastSession();
            }
        },
        fit: function () {
            if (view) {
                view.zoomToFit();
            }
        },
        zoomBy: function (factor) {
            if (view) {
                view.zoomBy(factor);
            }
        },
    };
}());
