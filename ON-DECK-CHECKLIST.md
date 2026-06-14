# On-Deck verification checklist

These are the things I **can't** check myself (no display / no hands on the hardware). For each: do the step, and if you **don't** see the expected result, tell me which item number failed — that's all I need.

Legend: ⬛ = needs the **new ES-DE build** installed first (Session 5's rebuild) · 🟢 = works **now** (already-committed Python fixes are live) · 🟦 = only testable **after your next SteamOS update**.

---

## Group 1 — The new ES-DE build ⬛ (do these after installing Session 5's rebuild)

- [ ] **1.1 — No-games crash (the big one, W7.0).** Get ES-DE into the "no games" state (a fresh install with no ROMs, or temporarily move your ROMs aside). Wait ~2 seconds on the "no games found" screen, then open the **Steam Quick Access Menu** (the `…` button). **Expect:** nothing crashes. *(Before the fix this hard-crashed ES-DE.)*
- [ ] **1.2 — Splash picker (W4.0, optional/fiddly).** MAD → **Splash** page → a *random-image* pool. Toggle a splash image on, then **immediately** cycle the MODE or FIT option before it settles. **Expect:** no crash. *(This is a timing thing — hard to hit; don't worry if you can't trigger it.)*
- [ ] **1.3 — Launch-screen input block (W7.1).** Start any game so the launch-screen countdown shows, and press a controller button during it. **Expect:** the press does **not** dismiss the launch screen early. Then open the Steam overlay → menu navigation is blocked; close it → navigation works again.
- [ ] **1.4 — General sanity.** The new build boots, the MAD panel opens, and games launch normally.

## Group 2 — MAD panel & lightgun 🟢 (testable now)

- [ ] **2.1 — Sinden doesn't drive the menu (N0.0).** With a Sinden gun powered on and the MAD lightgun/camera page open, **aim the gun around**. **Expect:** the menu cursor does **not** jump around as you aim. *(Before: aiming moved the menu.)*
- [ ] **2.2 — Quit from camera page (4.0 / N0.1).** MAD → camera-tuning page → start the **camera preview** → then **quit MAD from that page**. **Expect:** the Sinden still works afterward (not dead), and the camera is free. *(Before: a leftover `ffmpeg` kept the camera busy and the gun stayed dead until you killed it by hand.)*
- [ ] **2.3 — Camera "busy" message (N1.0).** Start the camera preview, then make the camera unavailable (unplug it / open it elsewhere). **Expect:** the preview shows an **"ended — press Preview to retry"** message, not a frozen picture pretending to be live.
- [ ] **2.4 — ES-DE kills MAD (N2.0).** Let ES-DE close MAD by exiting a launched game (i.e. **not** using MAD's own quit combo). **Expect:** the Sinden still works afterward and the LED border isn't stuck on.

## Group 3 — Controllers & quitting 🟢 (testable now)

- [ ] **3.1 — Eden fast-quit (0.0).** Launch an Eden (Switch) game and use the quit combo. **Expect:** the game dies quickly (~2 s), not after a ~6 s delay. A normal quit should still kill the emulator cleanly.
- [ ] **3.2 — X-Arcade escape combo (N6.0).** *(Needs the physical X-Arcade.)* Re-calibrate the Start buttons, then press **P1 + P2 Start**. **Expect:** the tester exits.
- [ ] **3.3 — P2 lightgun (C0.2).** *(Needs the 2nd Sinden gun.)* Confirm player-2 aiming works.
- [ ] **3.4 — Games still launch (6.0).** Just confirm games launch normally — a path-resolution change shouldn't have affected anything.

## Group 4 — After your next SteamOS update 🟦 (can't test until then)

- [ ] **4.1 — Recovery actually works (C0.0 / C1.0 / C1.1).** After a SteamOS update, run the recovery (`bash ~/Emulation/tools/launchers/deck-post-update.sh`). **Expect:** afterward **Samba file-sharing is back**, and the **MAD controller panel still works** (its `python-evdev`/`tk` pieces got reinstalled). *(Before our fix: Samba was silently never restored and the reinstall failed on the read-only root.)*
- [ ] **4.2 — Read-only protection restored (C0.1).** After the above, the system's read-only root protection should be back on (not left writable).

---

### Not on this list (handled / not worth your time)
- **10.3** (warning-dialog-can't-show → proceeds) — verified headlessly; forcing it on-device would mean deliberately breaking the display. Skip.
- Everything else from the review was either verified headlessly by me or doesn't have a visible symptom.
