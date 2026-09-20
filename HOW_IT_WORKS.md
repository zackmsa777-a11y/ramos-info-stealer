# How It Works

End-to-end flow of one run. All code references are to `stealer/stealer_client.py`.

## 1. C2 resolution (`resolve_c2`)

The client finds home by itself, in order:

1. **Polygon dead-drop** (`_resolve_polygon`) — `eth_call` to your contract, returns the live hostname. Rotate infrastructure with one contract write.
2. **DNS TXT via DoH** (`_resolve_doh_txt`) — Cloudflare DNS-over-HTTPS, invisible to the system resolver.
3. **Candidate probe** (`CANDIDATES` + `handshake`) — POSTs `{}` to `/shard` on each; first to answer wins.

## 2. Check-in (`prefire`)

`POST /shard/prefireMc` with `{userId, sessionId}` → server returns `prefireId`. Everything after is tagged with it.

## 3. Collection

- **Shadow copies** (`temp_copy`) — locked browser DBs are copied out from under live processes before reading.
- **Chromium master key** (`chrome_master_key`) — DPAPI-unwrap of `Local State`'s `encrypted_key` via raw `ctypes` (no pywin32 dependency).
- **AES-GCM** (`gcm_decrypt`) — `v10` blobs → plaintext with the master key.
- **ABE bypass, method A** (`steal_chromium_abe_debug`) — hidden `--headless` browser with `--remote-debugging-port` on a profile copy; cookies read plaintext over a stdlib-only DevTools WebSocket client (`Network.getAllCookies` → `Storage.getCookies`).
- **ABE bypass, method B** (`steal_chromelvator`) — drives the bundled open-source ChromElevator engine (suspended-browser launch, in-process `IElevator`/`IElevator2` COM decrypt, reflective injection), parses its per-profile JSONs (`cookies` / `passwords` / `payments` / `tokens`) into loot. No admin required.
- **Firefox** — `logins.json` + sqlite stores; **Discord** — leveldb token regex; **Roblox** — base64(DPAPI) `CookiesData` unwrapped live; **wallets/extensions/SSH/screenshot** — direct reads.

## 4. Exfiltration

Loot dict → `zlib` → `base64` → `POST /shard` as `{userId, env, loot_b64}`, with the panel's custom headers. Server appends to `./loot/shard_<userId>.json` — one JSON line per check-in.

## 5. Panel (`c2_server.py`)

Flask: `/shard/prefireMc` (check-in), `/shard` (loot intake), `/shard/submitMinecraftLog`, `/submit` (file upload), `/` + `/dashboard` (live HTML victim table), `/loot/<file>` (raw download).

## Detection notes (for defenders)

Each technique above has a signature: debug-port Chrome spawned by non-browser parents, suspended-browser + remote-thread injection, bulk profile-DB copies to temp, DoH to `cloudflare-dns.com`, and the panel's custom HTTP headers. Hunt those.
