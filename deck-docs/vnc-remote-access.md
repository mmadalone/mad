# Remote desktop access to the Steam Deck — VNC (x11vnc), 2026-06-22

Goal: reach KDE **Desktop Mode** from a PC with **RealVNC Viewer** (standard VNC/RFB), surviving
SteamOS updates. SOLUTION = x11vnc (in a distrobox) on the **X11** desktop session.

## The decisive facts (verified on-device 2026-06-22)
- SteamOS Desktop Mode can boot as **X11** (real Xorg + kwin_x11) OR **Wayland** (kwin_wayland +
  rootless Xwayland). The recent update flipped the **default** to Wayland (`steamosctl
  get-default-desktop-session` = plasma.desktop). Both session files still ship, so X11 is
  still selectable: `/usr/share/xsessions/plasmax11.desktop` + `/usr/share/wayland-sessions/plasma.desktop`.
- **x11vnc only works on the X11 session.** On a real Xorg, `xdpyinfo :0` shows a real
  1920x1080 depth-24 root window → x11vnc XGetImage works. On the **Wayland** session Xwayland
  is `-rootless` (no root framebuffer) → `XGetImage` BadMatch → **black screen**. (This is why
  it failed earlier in the evening while the session happened to be Wayland.)
- Desktop Mode here is **bare-metal Xorg from SDDM** (Xorg<-sddm-helper<-sddm), NOT nested in
  gamescope. (An earlier "nested in gamescope" guess was wrong — it came from reading a session
  that was flipping X11<->Wayland during testing.)

## What was set up (all on /home → survives updates)
- `~/vnc-start.sh` — (1) **self-heals the pin**: re-applies `steamosctl set-default-desktop-session
  plasmax11.desktop` at every start (idempotent) so even if you enter Desktop after an update before
  running deck-post-update.sh, the NEXT entry is X11; (2) waits for a REAL Xorg + the X0 socket (NOT
  Xwayland — that hangs on X11); (3) reads the **live** X authority (random per-login suffix
  `/run/user/1000/xauth_XXXXXX` — never hardcode) with a `[ -f "$XAUTH" ]` guard (exit→retry, never
  pass a bare `/run/host` dir to -auth); (4) `distrobox enter vnc-box -- x11vnc -auth /run/host<xauth>
  -display :0 -forever -shared -noxdamage -nopw`. (`-nopw` = passwordless per owner; switch to `-usepw`
  to require `~/.vnc/passwd`.) Container sees host paths under /run/host, X0 socket at /tmp/.X11-unix/X0.
- `vnc-distrobox.service` (systemd --user, enabled, WantedBy=graphical-session.target, **Restart=always**
  + StartLimitIntervalSec=0) runs it. Restart=always (not on-failure) because x11vnc exits 0 when the X
  server dies, so it auto-recovers an in-session Xorg restart and re-reads the fresh xauth.
- distrobox `vnc-box` (archlinux) has x11vnc 0.9.17 (host has NO x11vnc binary — must use the box).
- **Session pin:** `steamosctl set-default-desktop-session plasmax11.desktop` (granular — leaves
  login-mode=game so the Deck still BOOTS to Game Mode; "Switch to Desktop" now gives X11).
  Do NOT use `steamos-session-select plasma-x11-persistent` — it ALSO sets login-mode=desktop.
- **Update survival:** `deck-post-update.sh` re-applies the pin, self-gated on
  `systemctl --user is-enabled vnc-distrobox.service` (so it never forces X11 on a Wayland user).
  Needed because a SteamOS update resets the default back to Wayland.

## Connect
RealVNC Viewer (or any VNC client) -> `192.168.2.103:5900` (or :180). Passwordless (RealVNC may
warn "unencrypted/no auth" — expected on a firewall-less LAN; SteamOS has no firewall). To require
a password: edit `~/vnc-start.sh` `-nopw`->`-usepw`, set it with
`distrobox enter vnc-box -- x11vnc -storepasswd <pw> ~/.vnc/passwd`, restart the service.

## Caveats / OWED
- **Reboot test OWED** (couldn't reboot for the owner): after a reboot, switch to Desktop and
  confirm it comes up Plasma **X11** (loginctl Type=x11, kwin_x11, no Xwayland) and VNC auto-binds.
  NB: per the SteamOS-update memory, **unplug USB drives from the dock before rebooting** or it hangs.
- **Update test OWED**: re-pin survival across the NEXT SteamOS update is unproven; the post-update
  hook should handle it — re-verify `steamosctl get-default-desktop-session` after the next update.
- x11vnc serves only when a real Xorg exists; on a Wayland session the service idles (no black).
- `deck-post-update.sh` edit is UNCOMMITTED in the launchers repo (owner hasn't asked to commit).

## Dead ends (do NOT retry)
- KRdp (krdpserver, RDP not VNC): black screen — needs KWin Wayland screencast which fails from a
  service (wl_display unreachable / privileged-protocol denial / VAAPI encode fail). Also RealVNC
  can't speak RDP. KRdp service was set up then disabled. Old notes in `krdp-desktop-remote.md`.
- wayvnc = wlroots only (KWin is not wlroots). krfb (flatpak) = Wayland fallback but shares the
  failing portal stack + may need an on-screen click; not used.

Sources: on-device probes + 6-agent investigation workflow 2026-06-22; `steamos-session-select`
(reads as `steamosctl` wrapper); x11vnc 0.9.17.
