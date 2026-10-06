#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电子礼簿（Gift Book）NAS 持久化后端
=====================================

作用
----
原版「电子礼簿」是纯前端应用，数据只存在浏览器的 IndexedDB 里。
本服务为它补上一个 NAS 端的数据仓库，实现：

  1. 数据落在 NAS 磁盘上（不再依赖浏览器缓存，清缓存不丢数据）
  2. 多设备共享同一份礼簿（手机 / 电脑打开看到同一批数据）
  3. 顺带充当静态文件服务器（同源，无跨域问题）

设计要点
--------
* 零第三方依赖，只用 Python 标准库（镜像 python:3-alpine 即可运行）
* 数据是「密文」：业务内容由浏览器端 AES-256 加密后才提交，
  本服务不接触明文、也不知道用户密码，仅按 id 存取。
  排序用的 guestLevelWeight / levelUpdateTime 是明文索引字段，无需解密。
* 写入采用「临时文件 + 原子替换」，避免断电 / 中断导致库文件损坏
* 每次读盘前自动备份上一版（.bak），并提供 /api/export 手动导出
* id 由服务端统一分配，保证多设备之间 id 不冲突

接口
----
GET    /api/health             健康检查 + 统计
GET    /api/events             全部事项
GET    /api/events/<id>        单个事项
POST   /api/events             新增事项 -> {"id": n}
PUT    /api/events/<id>        更新（不存在则插入）
DELETE /api/events/<id>        删除（级联删除其下礼金）
GET    /api/gifts              全部礼金（可用 ?eventId= 过滤）
GET    /api/gifts/<id>         单条礼金
POST   /api/gifts              新增礼金 -> {"id": n}
PUT    /api/gifts/<id>         更新
DELETE /api/gifts/<id>         删除
GET    /api/export             下载完整库文件（JSON 附件）
POST   /api/restore            用上传的 JSON 全量覆盖（灾难恢复用）
"""

import json
import os
import posixpath
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ---------------------------------------------------------------- 配置

DATA_DIR = os.environ.get("GB_DATA_DIR", "/data")
STATIC_DIR = os.environ.get("GB_STATIC_DIR", "/home/static")
PORT = int(os.environ.get("GB_PORT", "3000"))
DB_FILE = os.path.join(DATA_DIR, "giftbook-db.json")
BAK_FILE = DB_FILE + ".bak"

# 访问口令：
#   为空  -> 不鉴权（默认，适用内网直连）
#   非空  -> 所有 /api/* 必须携带 X-GB-Token 请求头（或 ?token= 查询参数）
# 建议：一旦要把 8094 通过内网穿透/公网暴露，务必设置本项。
TOKEN = (os.environ.get("GB_TOKEN") or "").strip()

# 单次请求体上限，防止超大 body 把内存打满（默认 2MB）
MAX_BODY = int(os.environ.get("GB_MAX_BODY") or (2 * 1024 * 1024))

# 允许的集合（对应原版 IndexedDB 的 objectStore 名称）
STORES = ("events", "gifts")
# 可用于 GET 集合过滤的查询参数（索引名 -> 字段名）
FILTERABLE = {
    "eventId": "eventId",
    "id": "id",
}

_lock = threading.RLock()          # 保护 _state 与磁盘写入
_state = None                      # 内存中的数据库
_write_count = 0                   # 本进程累计写盘次数


# ---------------------------------------------------------------- 数据库

def _empty_state():
    return {
        "version": 1,
        "rev": 0,
        "events": [],
        "gifts": [],
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updatedAt": None,
    }


def _next_id(store):
    """在当前集合中取最大 id + 1（比计数器更抗数据迁移）"""
    items = _state.get(store) or []
    top = 0
    for it in items:
        try:
            v = int(it.get("id") or 0)
            if v > top:
                top = v
        except (TypeError, ValueError):
            continue
    return top + 1


def load_state():
    """从磁盘加载数据库；文件不存在或损坏时退回空库"""
    global _state
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            state = _empty_state()
            state.update(data if isinstance(data, dict) else {})
            for store in STORES:
                if not isinstance(state.get(store), list):
                    state[store] = []
            state["rev"] = int(state.get("rev") or 0)
            _state = state
            print(
                "[giftbook] 已载入数据：%d 个事项 / %d 条礼金 (rev=%d)"
                % (len(state["events"]), len(state["gifts"]), state["rev"]),
                flush=True,
            )
            return
        except Exception as exc:  # noqa: BLE001
            print("[giftbook] 数据库文件损坏，尝试使用备份：%s" % exc, flush=True)
            if os.path.exists(BAK_FILE):
                try:
                    with open(BAK_FILE, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    state = _empty_state()
                    state.update(data)
                    _state = state
                    print("[giftbook] 已从备份恢复", flush=True)
                    return
                except Exception as exc2:  # noqa: BLE001
                    print("[giftbook] 备份也无法读取：%s" % exc2, flush=True)
    _state = _empty_state()
    print("[giftbook] 初始化空数据库", flush=True)


def save_state():
    """原子写盘：先写临时文件并 fsync，再 os.replace 覆盖"""
    global _write_count
    os.makedirs(DATA_DIR, exist_ok=True)
    _state["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload = json.dumps(_state, ensure_ascii=False, separators=(",", ":"))

    # 保留上一版作为备份
    if os.path.exists(DB_FILE):
        try:
            if not os.path.exists(BAK_FILE) or os.path.getmtime(DB_FILE) > os.path.getmtime(BAK_FILE):
                with open(DB_FILE, "rb") as src, open(BAK_FILE, "wb") as dst:
                    dst.write(src.read())
        except Exception:  # noqa: BLE001
            pass

    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, DB_FILE)
    _write_count += 1


def _bump_rev():
    _state["rev"] = int(_state.get("rev") or 0) + 1


def db_add(store, obj):
    with _lock:
        new_id = _next_id(store)
        record = dict(obj) if isinstance(obj, dict) else {}
        record["id"] = new_id
        _state[store].append(record)
        _bump_rev()
        save_state()
        return new_id


def db_update(store, obj):
    """按 id 就地更新；不存在则插入（upsert，供数据迁移使用）

    注意：这里是「字段合并」而不是「整体替换」。
    前端调用 update 时可能只传变化的字段（例如单独改宾客等级），
    若整体替换会把 eventId 等关键字段冲掉，导致记录从查询结果中消失。
    """
    with _lock:
        record = dict(obj) if isinstance(obj, dict) else {}
        oid = record.get("id")
        if oid is None:
            return db_add(store, record)
        arr = _state[store]
        for idx, item in enumerate(arr):
            if item.get("id") == oid:
                merged = dict(item)
                merged.update(record)
                arr[idx] = merged
                _bump_rev()
                save_state()
                return oid
        arr.append(record)
        _bump_rev()
        save_state()
        return oid


def db_delete(store, oid):
    with _lock:
        before = len(_state[store])
        _state[store] = [it for it in _state[store] if it.get("id") != oid]
        removed = before - len(_state[store])
        # 删除事项时级联清理其下礼金，避免产生孤儿数据
        if store == "events":
            _state["gifts"] = [g for g in _state["gifts"] if g.get("eventId") != oid]
        if removed:
            _bump_rev()
            save_state()
        return removed


def db_get(store, oid):
    with _lock:
        for item in _state[store]:
            if item.get("id") == oid:
                return item
        return None


def db_list(store, filters):
    with _lock:
        items = list(_state[store])
        for field, raw in filters.items():
            try:
                want = int(raw)
            except (TypeError, ValueError):
                want = raw
            items = [it for it in items if it.get(field) == want]
        return items


# ---------------------------------------------------------------- 静态文件

MIME_EXTRA = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


def guess_mime(path):
    ext = posixpath.splitext(path)[1].lower()
    return MIME_EXTRA.get(ext, "application/octet-stream")


def resolve_static(url_path):
    """把 URL 路径安全地映射到 STATIC_DIR 内的真实文件（防目录穿越）"""
    rel = urllib.parse.unquote(url_path.split("?", 1)[0]).lstrip("/")
    if rel in ("", "."):
        rel = "index.html"
    candidate = os.path.normpath(os.path.join(STATIC_DIR, rel))
    root = os.path.normpath(STATIC_DIR)
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    if os.path.isdir(candidate):
        candidate = os.path.join(candidate, "index.html")
    return candidate if os.path.isfile(candidate) else None


# ---------------------------------------------------------------- HTTP

class RequestError(Exception):
    """带 HTTP 状态码的请求错误"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "GiftBookNAS/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础工具 ----

    def log_message(self, fmt, *args):
        # 静态资源太吵，只记录 API 调用
        try:
            line = fmt % args
        except Exception:  # noqa: BLE001
            line = fmt
        if "/api/" in line or "error" in line.lower():
            sys.stderr.write("[giftbook] %s %s\n" % (self.log_date_time_string(), line))

    def _send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 刻意不发送 Access-Control-Allow-Origin：
        # 网页与接口同源部署，本来就不需要 CORS；一旦开放通配，
        # 用户浏览器上的任意第三方站点就能 fetch 到内网这台 NAS 的礼簿数据。
        self.end_headers()
        if self.command != "HEAD":  # HEAD 不应带响应体，否则会污染 keep-alive 连接
            self.wfile.write(body)

    def _send_text(self, code, text, mime="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self):
        """
        读取并解析 JSON 请求体。
        统一在这里做长度校验，避免超大 body 把内存打满；失败抛 RequestError。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            raise RequestError(400, "请求体为空")
        if length > MAX_BODY:
            # 尽量把已到达的数据读掉再回绝：否则客户端可能只收到连接重置(RST)，
            # 拿不到 413 状态码，排查时容易误判为「网络断了」
            try:
                self.rfile.read(min(length, MAX_BODY + 65536))
            except Exception:  # noqa: BLE001
                pass
            raise RequestError(413, "请求体过大（上限 %d 字节）" % MAX_BODY)
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RequestError(400, "JSON 解析失败: %s" % exc)

    def _read_json_safe(self):
        """读 body 失败时直接回错误并返回 None"""
        try:
            return self._read_json()
        except RequestError as exc:
            self._send_json(exc.code, {"error": exc.message})
        return None

    # ---- 鉴权 ----

    def _authorized(self):
        """未配置口令时一律放行；配置后校验请求头 / 查询参数"""
        if not TOKEN:
            return True
        got = self.headers.get("X-GB-Token") or ""
        if not got:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            got = (query.get("token") or [""])[0]
        return secrets.compare_digest(got, TOKEN)

    def _guard_api(self):
        """API 请求的鉴权闸门；未通过时已回 401，返回 False"""
        if self._authorized():
            return True
        self._send_json(401, {"error": "unauthorized", "hint": "缺少或错误的访问口令"})
        return False

    def _parse_api(self, path):
        """返回 (store, oid)；非集合接口返回 (None, None)"""
        parts = [p for p in path.split("/") if p]
        # 形如 ["api", "events"] 或 ["api", "events", "5"]
        if len(parts) >= 2 and parts[0] == "api" and parts[1] in STORES:
            oid = None
            if len(parts) >= 3 and parts[2] != "":
                try:
                    oid = int(parts[2])
                except ValueError:
                    oid = parts[2]
            return parts[1], oid
        return None, None

    # ---- OPTIONS（预检） ----

    def do_OPTIONS(self):  # noqa: N802
        # 同源部署不会产生预检请求；这里只做最小响应，不下发任何 CORS 放行头
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- GET ----

    def do_GET(self):  # noqa: N802
        # 用 urlsplit 而非 urlparse：后者会把路径里 ";" 之后的内容当 params 静默截掉
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 健康检查不鉴权：容器 healthcheck 与前端可用性探测都依赖它，
        # 且只返回条目数量等统计信息，不泄露任何礼金内容。
        if path == "/api/health":
            with _lock:
                self._send_json(200, {
                    "ok": True,
                    "service": "giftbook-nas",
                    "authRequired": bool(TOKEN),
                    "rev": _state.get("rev", 0),
                    "events": len(_state["events"]),
                    "gifts": len(_state["gifts"]),
                    "updatedAt": _state.get("updatedAt"),
                    "writes": _write_count,
                })
            return

        if path.startswith("/api/") and not self._guard_api():
            return

        if path == "/api/export":
            with _lock:
                body = json.dumps(_state, ensure_ascii=False, indent=2).encode("utf-8")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="giftbook-backup-%s.json"' % stamp,
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        store, oid = self._parse_api(path)
        if store:
            if oid is None:
                filters = {field: query[key][0] for key, field in FILTERABLE.items() if key in query}
                self._send_json(200, db_list(store, filters))
            else:
                item = db_get(store, oid)
                if item is None:
                    self._send_json(404, {"error": "not found"})
                else:
                    self._send_json(200, item)
            return

        self._serve_static(path)

    # ---- POST（新增 / 恢复） ----

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/") and not self._guard_api():
            return

        if path == "/api/restore":
            data = self._read_json_safe()
            if data is None:
                return
            if not isinstance(data, dict) or not isinstance(data.get("events"), list):
                self._send_json(400, {"error": "bad payload"})
                return
            with _lock:
                # 覆盖式恢复属于破坏性操作，先落一份带时间戳的快照以便误操作后找回
                try:
                    snap_dir = os.path.join(DATA_DIR, "snapshots")
                    os.makedirs(snap_dir, exist_ok=True)
                    snap_name = "pre-restore-%s.json" % time.strftime("%Y%m%d-%H%M%S")
                    with open(os.path.join(snap_dir, snap_name), "w", encoding="utf-8") as fh:
                        json.dump(_state, fh, ensure_ascii=False)
                    olds = sorted(f for f in os.listdir(snap_dir) if f.endswith(".json"))
                    for old in olds[:-20]:  # 只保留最近 20 份
                        os.remove(os.path.join(snap_dir, old))
                except Exception as exc:  # noqa: BLE001
                    print("[giftbook] 恢复前快照失败：%s" % exc, flush=True)

                state = _empty_state()
                state["events"] = data.get("events") or []
                state["gifts"] = data.get("gifts") or []
                state["rev"] = int(data.get("rev") or 0)
                state["createdAt"] = data.get("createdAt") or state["createdAt"]
                _state.clear()
                _state.update(state)
                _bump_rev()
                save_state()
            self._send_json(200, {"ok": True, "events": len(_state["events"]), "gifts": len(_state["gifts"])})
            return

        store, oid = self._parse_api(path)
        if store and oid is None:
            payload = self._read_json_safe()
            if payload is None:
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "payload must be an object"})
                return
            new_id = db_add(store, payload)
            self._send_json(200, {"id": new_id})
            return

        self._send_json(404, {"error": "not found"})

    # ---- PUT（更新） ----

    def do_PUT(self):  # noqa: N802
        if not self._guard_api():
            return
        store, oid = self._parse_api(urllib.parse.urlsplit(self.path).path)
        if not store:
            self._send_json(404, {"error": "not found"})
            return
        payload = self._read_json_safe()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "payload must be an object"})
            return
        if oid is not None:
            payload.setdefault("id", oid)
        saved = db_update(store, payload)
        self._send_json(200, {"id": saved})

    # ---- DELETE ----

    def do_DELETE(self):  # noqa: N802
        if not self._guard_api():
            return
        store, oid = self._parse_api(urllib.parse.urlsplit(self.path).path)
        if not store or oid is None:
            self._send_json(404, {"error": "not found"})
            return
        removed = db_delete(store, oid)
        self._send_json(200, {"removed": removed})

    # ---- 静态文件 ----

    def _serve_static(self, path):
        target = resolve_static(path)
        if not target:
            self._send_text(404, "404 Not Found")
            return
        try:
            with open(target, "rb") as fh:
                body = fh.read()
        except OSError as exc:
            self._send_text(500, "read error: %s" % exc)
            return
        mime = guess_mime(target)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        # html 不缓存（改完刷新即生效），静态资源短缓存
        if mime.startswith("text/html"):
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()


# ---------------------------------------------------------------- 启动

def main():
    load_state()
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(DB_FILE):
        with _lock:
            save_state()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.daemon_threads = True
    print("[giftbook] 服务已启动 http://0.0.0.0:%d" % PORT, flush=True)
    print("[giftbook] 静态目录 %s / 数据目录 %s" % (STATIC_DIR, DATA_DIR), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[giftbook] 收到中断，退出", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
