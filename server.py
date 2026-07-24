#!/usr/bin/env python3
"""ピクセルバーチャルオフィス リアルタイムサーバー
Python標準ライブラリのみで動作（追加インストール不要）。
起動: python3 server.py  （ポートは環境変数 PORT、デフォルト 8933）
"""
import json
import os
import threading
import time
import uuid
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
PORT = int(os.environ.get("PORT", "8933"))
MAX_PLAYERS = 50
TICK = 0.1          # SSE配信間隔（秒）
STALE_SEC = 20      # この秒数ハートビートが無いプレイヤーは退室扱い

# チャット永続化（Upstash Redis REST API）。環境変数が無ければメモリのみで動作。
UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
CHAT_KEY = "chat:log"
CHAT_KEEP = 200     # 永続保存する最大件数

state_lock = threading.Lock()
players = {}        # id -> {name,color,x,y,moving,status,last_seen}
events = []         # [(seq, event_dict)] 新しいイベントの追記ログ
seq_counter = 0
room_locks = {"A": False, "B": False}
chat_history = []   # 直近のチャット（入室時に送る／Upstashのキャッシュ）


def upstash(cmd):
    """Upstash REST APIでRedisコマンドを1つ実行。未設定/失敗時は None。"""
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return None
    try:
        data = json.dumps(cmd).encode("utf-8")
        req = urllib.request.Request(
            UPSTASH_URL, data=data,
            headers={"Authorization": "Bearer " + UPSTASH_TOKEN,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8")).get("result")
    except (urllib.error.URLError, ValueError, OSError) as e:
        print("upstash error:", e)
        return None


def persist_chat(ev):
    """チャット/メガホンをUpstashへ保存（別スレッドでノンブロッキング）。"""
    if not (UPSTASH_URL and UPSTASH_TOKEN):
        return

    def _do():
        upstash(["RPUSH", CHAT_KEY, json.dumps(ev, ensure_ascii=False)])
        upstash(["LTRIM", CHAT_KEY, str(-CHAT_KEEP), "-1"])

    threading.Thread(target=_do, daemon=True).start()


def load_chat():
    """起動時にUpstashから履歴を読み込む（再起動・スリープ後も残す）。"""
    res = upstash(["LRANGE", CHAT_KEY, "0", "-1"])
    if not res:
        return
    for item in res:
        try:
            chat_history.append(json.loads(item))
        except (ValueError, TypeError):
            pass
    if len(chat_history) > CHAT_KEEP:
        del chat_history[:len(chat_history) - CHAT_KEEP]
    print("loaded %d chat messages from Upstash" % len(chat_history))


def add_event(ev):
    global seq_counter
    seq_counter += 1
    events.append((seq_counter, ev))
    if len(events) > 600:
        del events[:300]
    if ev.get("type") in ("chat", "mega"):
        chat_history.append(ev)
        if len(chat_history) > CHAT_KEEP:
            del chat_history[:len(chat_history) - CHAT_KEEP]
        persist_chat(ev)


def prune_loop():
    while True:
        time.sleep(5)
        now = time.time()
        with state_lock:
            gone = [pid for pid, p in players.items() if now - p["last_seen"] > STALE_SEC]
            for pid in gone:
                name = players[pid]["name"]
                del players[pid]
                add_event({"type": "leave", "id": pid, "name": name})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # ---------- helpers ----------
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 100000:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def write_chunk(self, data: bytes):
        self.wfile.write(("%X\r\n" % len(data)).encode() + data + b"\r\n")
        self.wfile.flush()

    # ---------- GET ----------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/events":
            self.handle_sse()
        elif path == "/api/ping":
            self.send_json({"ok": True, "players": len(players)})
        else:
            self.serve_static(path)

    def serve_static(self, path):
        if path == "/":
            path = "/index.html"
        fpath = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
        if not fpath.startswith(ROOT) or not os.path.isfile(fpath):
            self.send_json({"error": "not found"}, 404)
            return
        ctypes = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                  ".css": "text/css", ".png": "image/png", ".ico": "image/x-icon"}
        ext = os.path.splitext(fpath)[1]
        with open(fpath, "rb") as fp:
            body = fp.read()
        self.send_response(200)
        self.send_header("Content-Type", ctypes.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_sse(self):
        qs = parse_qs(urlparse(self.path).query)
        pid = (qs.get("id") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        with state_lock:
            last_seq = seq_counter
        try:
            while True:
                time.sleep(TICK)
                with state_lock:
                    if pid and pid in players:
                        players[pid]["last_seen"] = time.time()
                    snap = {p_id: [round(p["x"], 1), round(p["y"], 1),
                                   1 if p["moving"] else 0, p["status"],
                                   p["name"], p["color"],
                                   1 if p.get("guide") else 0]
                            for p_id, p in players.items()}
                    evs = [e for s, e in events if s > last_seq]
                    last_seq = seq_counter
                    locks = dict(room_locks)
                payload = json.dumps({"p": snap, "locks": locks, "ev": evs},
                                     ensure_ascii=False)
                self.write_chunk(("data: " + payload + "\n\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            # 切断されたら早めに退室扱いになるようタイムスタンプを巻き戻す
            with state_lock:
                if pid and pid in players:
                    players[pid]["last_seen"] = time.time() - STALE_SEC + 3

    # ---------- POST ----------
    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()
        if path == "/api/join":
            self.api_join(body)
        elif path == "/api/update":
            self.api_update(body)
        elif path == "/api/event":
            self.api_event(body)
        else:
            self.send_json({"error": "not found"}, 404)

    def api_join(self, body):
        name = str(body.get("name", "")).strip()[:10] or "ゲスト"
        color = str(body.get("color", "#d97757"))[:7]
        guide = bool(body.get("guide"))
        with state_lock:
            if len(players) >= MAX_PLAYERS:
                self.send_json({"error": "満室です（最大%d人）" % MAX_PLAYERS}, 403)
                return
            pid = uuid.uuid4().hex[:8]
            spawn = {"x": 245 + (len(players) % 7) * 5, "y": 282}
            players[pid] = {"name": name, "color": color, "guide": guide,
                            "x": spawn["x"], "y": spawn["y"],
                            "moving": False, "status": "active",
                            "last_seen": time.time()}
            add_event({"type": "join", "id": pid, "name": name})
            history = list(chat_history[-50:])   # 入室時は直近50件を渡す
            locks = dict(room_locks)
        self.send_json({"id": pid, "spawn": spawn, "chat": history, "locks": locks})

    def api_update(self, body):
        pid = body.get("id")
        with state_lock:
            p = players.get(pid)
            if not p:
                self.send_json({"error": "unknown player"}, 404)
                return
            if "x" in body:
                p["x"] = max(0.0, min(480.0, float(body["x"])))
            if "y" in body:
                p["y"] = max(0.0, min(300.0, float(body["y"])))
            p["moving"] = bool(body.get("moving", False))
            if body.get("status") in ("active", "busy", "away"):
                p["status"] = body["status"]
            p["last_seen"] = time.time()
        self.send_json({"ok": True})

    def api_event(self, body):
        pid = body.get("id")
        etype = body.get("type")
        with state_lock:
            p = players.get(pid)
            if not p:
                self.send_json({"error": "unknown player"}, 404)
                return
            p["last_seen"] = time.time()
            name = p["name"]
            if etype == "chat":
                text = str(body.get("text", "")).strip()[:60]
                if text:
                    add_event({"type": "chat", "id": pid, "name": name, "text": text})
            elif etype == "mega":
                text = str(body.get("text", "")).strip()[:60]
                if text:
                    add_event({"type": "mega", "id": pid, "name": name, "text": text})
            elif etype == "emote":
                emoji = str(body.get("emoji", ""))[:4]
                if emoji:
                    add_event({"type": "emote", "id": pid, "emoji": emoji})
            elif etype == "lock":
                room = body.get("room")
                if room in room_locks:
                    room_locks[room] = bool(body.get("locked"))
                    add_event({"type": "lock", "room": room,
                               "locked": room_locks[room], "name": name})
        self.send_json({"ok": True})


def main():
    if UPSTASH_URL and UPSTASH_TOKEN:
        print("Upstash chat persistence: ON")
        load_chat()
    else:
        print("Upstash chat persistence: OFF (メモリのみ。環境変数未設定)")
    threading.Thread(target=prune_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("Pixel Virtual Office server on http://0.0.0.0:%d" % PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
