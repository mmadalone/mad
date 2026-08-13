# KDE Desktop Mode remote access — KRdp (RDP)

Set up 2026-06-22, replacing the broken x11vnc-via-distrobox VNC setup for **Desktop Mode**.

## Why x11vnc could not work for Desktop Mode
- SteamOS Desktop Mode = **KDE Plasma 6 Wayland**; its Xwayland runs **`-rootless`**
  (`/usr/bin/Xwayland :0 ... -rootless`).
- x11vnc grabs the screen via `XGetImage` on the root window. A rootless Xwayland has
  **no root framebuffer** → `X Error: BadMatch (X_GetImage)` → cannot capture.
- Newer x11vnc (0.9.17) also *exits* in a Wayland session: "Wayland sessions are as of
  now only supported via -rawfb ... Exiting." (triggered when `WAYLAND_DISPLAY` is in env,
  e.g. under the graphical-session systemd context).
- x11vnc **does** work in **Game Mode** (gamescope's Xwayland is rooted) — that path's
  launcher bug (waited for `Xorg`, which never exists on SteamOS — it's `Xwayland`) was
  fixed in `~/vnc-start.sh`; service `vnc-distrobox.service` left **disabled** but intact.

## The working solution: KRdp
- `krdp` package (`/usr/bin/krdpserver`, v6.4.3) **ships in the SteamOS image** → survives
  updates. Captures KWin via PipeWire / `--plasma` screencast protocol → works on rootless
  Wayland. Protocol is **RDP**, not VNC.
- Service: `~/.config/systemd/user/krdp-desktop.service`
  - `ExecStart=/usr/bin/krdpserver --plasma -u deck -p "" --certificate .../cert.pem --certificate-key .../key.pem --port 3389`
  - `--plasma` = capture KWin directly, **no portal permission dialog**, no KWallet.
  - `WantedBy=plasma-workspace.target` → **auto-starts in Desktop Mode only** (no KDE session
    in Game Mode = nothing to share — inherent limit).
- TLS cert: `~/.local/share/krdp/{cert.pem,key.pem}` (self-signed, 10yr, 0600). Clients show
  a one-time "untrusted certificate" warning — normal, accept it.
- **Passwordless** (user `deck`, empty password) by user request. ⚠️ Anyone on the LAN can
  control the desktop. SteamOS has no host firewall by default. Trusted-LAN only.

## Connect
- Any RDP client → `192.168.2.x:3389` (or `steamdeck` / `steamdeck.local`), user `deck`,
  blank password, accept cert. Windows "Remote Desktop Connection", Remmina (RDP), or
  Microsoft Remote Desktop (iOS/Android/Mac).
- **Deck must be in Desktop Mode** for it to answer.

## Add a password later (if a client rejects blank, or for security)
Edit `~/.config/systemd/user/krdp-desktop.service`, change `-p ""` to `-p "yourpass"`, then:
`systemctl --user daemon-reload && systemctl --user restart krdp-desktop.service`

## Built-in alternative (not used)
KDE ships `app-org.kde.krdpserver.service` + a System Settings → Remote Desktop KCM, but it
reads the password from **KWallet** (awkward headless). Our own unit uses CLI args instead.

Sources: `krdpserver --help` (KRdp 6.4.3); KDE KRdp docs (invent.kde.org/network/krdp);
on-device verification 2026-06-22 (port bind confirmed; client login handshake = owner to verify).
