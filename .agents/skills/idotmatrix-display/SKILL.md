---
name: idotmatrix-display
description: >-
  Use this skill whenever the user asks to operate or control their iDotMatrix
  LED pixel display (32x32), including showing pictures or logos (Google Cloud,
  Google G, Antigravity, or custom images on a black background), running or
  stopping a full-screen LED pixel countdown timer, adjusting brightness, or
  managing the iDotMatrix Companion web app.
---

# iDotMatrix Display Operator Skill

Operate an iDotMatrix `32x32` BLE LED pixel display using either the local **iDotMatrix Companion HTTP API** (`http://127.0.0.1:8080`) or direct Bluetooth Low Energy (BLE) commands via [`scripts/idm_ctl.py`](./scripts/idm_ctl.py).

## Overview & Capabilities

This skill provides two core features for controlling the iDotMatrix display:

1. **Feature 1 — Picture & Logo Display (on Black Screen)**:
   - Displays pre-defined logos from [`library/`](../../../library/) on a pure black `(0, 0, 0)` background:
     - `google-cloud` (`google-cloud.png`) — 4-color Google Cloud mark on black
     - `google-g` (`google-g.png`) — 4-color Google "G" on black
     - `antigravity` (`antigravity.png`) — Rainbow Antigravity "A" logo on black
     - `red-heart` (`red-heart.png`) — Vibrant red heart on black
     - `thumbs-up` (`thumbs-up.png`) — Thumbs up icon on black
   - Also supports displaying any custom image file on a pure black `(0, 0, 0)` background with `Image.Resampling.LANCZOS` anti-aliasing so curves look smooth on the `32x32` grid.

2. **Feature 2 — Full-Screen LED Pixel Countdown Timer**:
   - Lights up all `1,024` pixels (`32x32`) in a chosen color (default `#00ff00`) using DIY/Graffiti mode (`ImageMode.EnableDIY` + `GraffitiModule.set_pixels` in 255-pixel batches) so the device **never shows the upload/link icon**.
   - Extinguishes pixels to black `(0, 0, 0)` one by one across the configured duration using `time.monotonic()` drift-free pacing (`total_seconds / 1024` per pixel).

---

## Environment & Hardware Reference

- **Companion Web UI & API**: `http://127.0.0.1:8080`
- **BLE Device**: Auto-discovered by default (or specify via `--mac` / `IDOTMATRIX_MAC`, screen size `32x32` / `1,024` pixels)

---

## Step-by-Step Workflows

Always use the bundled helper script [`scripts/idm_ctl.py`](./scripts/idm_ctl.py).
`idm_ctl.py` automatically checks if the Companion server at `http://127.0.0.1:8080` is running and routes commands through its HTTP API (preventing BLE connection conflicts and keeping the browser UI synchronized), or falls back to a direct BLE connection if the server is offline.

### Workflow 1: Show a Pre-Defined Logo or Custom Picture

1. **Show one of the built-in logos (`google-cloud`, `google-g`, `antigravity`, `red-heart`, `thumbs-up`)**:
   ```sh
   python .agents/skills/idotmatrix-display/scripts/idm_ctl.py picture antigravity
   ```
   Supported built-in aliases:
   - `google-cloud` (or `cloud`, `gcp`)
   - `google-g` (or `g`, `google`)
   - `antigravity` (or `agy`)
   - `red-heart` (or `heart`)
   - `thumbs-up` (or `thumbsup`, `thumb`, `like`)

2. **Show a custom image file on a black background**:
   ```sh
   python .agents/skills/idotmatrix-display/scripts/idm_ctl.py picture path/to/image.png --strip-white-bg
   ```
   - Pass `--strip-white-bg` when the source image has a white card or squircle background that should be flood-filled to pure black `(0, 0, 0)` from the perimeter before displaying.

3. **Verify**:
   Check the JSON output of `idm_ctl.py` (or run `idm_ctl.py status`) to confirm `"active_mode": "picture"` and `"active_picture"` matches the requested image.

---

### Workflow 2: Run or Stop the LED Pixel Countdown Timer

1. **Start a countdown timer**:
   ```sh
   python .agents/skills/idotmatrix-display/scripts/idm_ctl.py countdown --seconds 60 --color "#00ff00"
   ```
   - `--seconds`: Duration in seconds (e.g., `60` for 1 minute, `300` for 5 minutes).
   - `--color`: Hex color for the lit countdown pixels (e.g., `#00ff00` green, `#4285f4` blue, `#ea4335` red, `#fbbc05` yellow).
   - `--wait`: Optional flag to block until the countdown finishes (automatic when running in direct BLE fallback mode).

2. **Check live countdown progress**:
   ```sh
   python .agents/skills/idotmatrix-display/scripts/idm_ctl.py status
   ```
   Inspect `countdown.running`, `countdown.remaining_seconds`, `countdown.lit_pixels`, and `countdown.percent`.

3. **Stop an active countdown early**:
   ```sh
   python .agents/skills/idotmatrix-display/scripts/idm_ctl.py stop
   ```

---

### Workflow 3: Ensure the Companion Web App is Running

If the user wants to use the web frontend at `http://127.0.0.1:8080`:

1. Check if the server is already listening:
   ```sh
   python .agents/skills/idotmatrix-display/scripts/idm_ctl.py status
   ```
2. If `"transport": "direct-ble"` is reported (meaning the web server is not running), start `app.py` as a background daemon task using `run_command` with `IsDaemon: true`:
   ```sh
   python app.py
   ```

---

## Quality Standards & Guardrails

- **Pure Black Backgrounds**: Always composite or pad images onto `(0, 0, 0)` so unlit LEDs remain completely off on the physical matrix.
- **Anti-Aliased Downscaling**: Always use `ImageMode.EnableDIY` + `ImageModule.upload_image_file(..., background_color=(0, 0, 0))` (which uses `Image.Resampling.LANCZOS`) for logos and photos rather than 1-bit or nearest-neighbor downscaling, or `show_image_file` (which sends static image type `0x02` that `32x32` firmware `4.12` rejects with `0500020000`).
- **No Upload Icon During Countdowns**: Never use full-frame image uploads (`upload_image_file`) inside a countdown loop; always use `ImageMode.EnableDIY` + `GraffitiModule.set_pixels` in batches of at most `255` pixels (`MAX_PIXEL_LIST_LENGTH`).
- **Detailed Protocol Reference**: See [`references/api_and_protocol.md`](./references/api_and_protocol.md) for HTTP payload schemas and BLE protocol details.
