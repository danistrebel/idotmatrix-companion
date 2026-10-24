"""
iDotMatrix Companion Web Application.

Provides a lightweight web frontend and JSON API to:
1. Display pictures from a pre-defined library (Google Cloud, Google G, Antigravity) on a black screen.
2. Run a full-screen LED pixel countdown timer (using DIY + Graffiti mode so no upload/link icon appears).
"""

import argparse
import asyncio
import base64
import io
import json
import logging
import mimetypes
import time
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from PIL import Image

from idotmatrix import IDotMatrixClient, ScreenSize
from idotmatrix.modules.graffiti import MAX_PIXEL_LIST_LENGTH
from idotmatrix.modules.image import ImageMode
from idotmatrix.util import color_utils

from init_library import (
    LIBRARY_DIR,
    ensure_library,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s :: %(levelname)s :: %(name)s :: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("bleak").setLevel(logging.WARNING)
logger = logging.getLogger("idotmatrix_companion")


class CompanionController:
    """Manages the iDotMatrix BLE connection, picture display, and pixel countdown task."""

    def __init__(self, mac_address: str | None = None, screen_size: ScreenSize | None = None) -> None:
        self.mac_address = mac_address
        self.default_screen_size = screen_size
        self.client: IDotMatrixClient | None = None
        self._ble_lock = asyncio.Lock()
        self.busy = False
        self.active_mode: str = "idle"
        self.active_picture: str | None = None
        self.status_message: str = "Ready"

        # Countdown state
        self._countdown_task: asyncio.Task[None] | None = None
        self.countdown_running: bool = False
        self.countdown_total_seconds: float = 0.0
        self.countdown_elapsed_seconds: float = 0.0
        self.countdown_total_pixels: int = 0
        self.countdown_cleared_pixels: int = 0
        self.countdown_color: str = "#00ff00"

    @property
    def is_connected(self) -> bool:
        return (
            self.client is not None
            and self.client._connection_manager is not None
            and self.client._connection_manager.is_connected()
        )

    async def ensure_connected(self) -> IDotMatrixClient:
        if self.is_connected and self.client is not None:
            return self.client

        self.status_message = "Connecting to iDotMatrix display..."
        logger.info("Connecting to iDotMatrix display (mac=%s)...", self.mac_address or "auto-discover")
        self.client = IDotMatrixClient(
            screen_size=self.default_screen_size,
            mac_address=self.mac_address,
            install_signal_handlers=False,
        )
        await self.client.connect()
        if self.client._connection_manager.address:
            self.mac_address = self.client._connection_manager.address
        self.status_message = f"Connected ({self.screen_size_label})"
        return self.client

    @property
    def screen_size_label(self) -> str:
        if self.client is not None and self.client.screen_size_known:
            w, h = self.client.screen_size.value
            return f"{w}x{h}"
        if self.default_screen_size is not None:
            w, h = self.default_screen_size.value
            return f"{w}x{h}"
        return "auto"

    async def disconnect(self) -> None:
        await self.stop_countdown()
        if self.client is not None:
            await self.client.disconnect()
        self.active_mode = "idle"
        self.status_message = "Disconnected"

    async def set_brightness(self, brightness: int) -> None:
        brightness = max(5, min(100, int(brightness)))
        async with self._ble_lock:
            client = await self.ensure_connected()
            await client.set_brightness(brightness)
            self.status_message = f"Brightness set to {brightness}%"

    async def show_picture(self, filename: str) -> None:
        file_path = (LIBRARY_DIR / Path(filename).name).resolve()
        if not file_path.exists() or file_path.parent != LIBRARY_DIR.resolve():
            raise FileNotFoundError(f"Picture '{filename}' not found in library")

        await self.stop_countdown()
        self.busy = True
        self.status_message = f"Sending {filename} to display..."
        try:
            async with self._ble_lock:
                client = await self.ensure_connected()
                await client.image.show_image_file(
                    file_path=file_path,
                    background_color=(0, 0, 0),
                )
            self.active_mode = "picture"
            self.active_picture = file_path.name
            self.status_message = f"Showing {file_path.name}"
        finally:
            self.busy = False

    async def start_countdown(self, total_seconds: float, color_hex: str = "#00ff00") -> None:
        if total_seconds <= 0:
            raise ValueError("Countdown duration must be greater than 0 seconds")

        await self.stop_countdown()
        color_rgb = color_utils.parse_color_rgb(color_hex)
        self.countdown_color = color_hex
        self.countdown_total_seconds = float(total_seconds)
        self.countdown_elapsed_seconds = 0.0
        self.countdown_cleared_pixels = 0
        self.countdown_running = True
        self.active_mode = "countdown"
        self.active_picture = None

        self._countdown_task = asyncio.create_task(
            self._run_pixel_countdown(total_seconds=float(total_seconds), color=color_rgb)
        )

    async def stop_countdown(self) -> None:
        if self._countdown_task is not None and not self._countdown_task.done():
            self._countdown_task.cancel()
            try:
                await self._countdown_task
            except asyncio.CancelledError:
                pass
        self._countdown_task = None
        if self.countdown_running:
            self.countdown_running = False
            self.status_message = "Countdown stopped"

    async def _run_pixel_countdown(self, total_seconds: float, color: tuple[int, int, int]) -> None:
        try:
            async with self._ble_lock:
                client = await self.ensure_connected()
                width, height = client.screen_size.value
                all_pixels = [(x, y) for y in range(height) for x in range(width)]
                total_pixels = len(all_pixels)
                self.countdown_total_pixels = total_pixels
                self.status_message = f"Lighting up all {total_pixels} pixels..."

                # 1. Enter DIY mode once and light up all pixels in 255-pixel batches (no upload icon)
                await client.image.set_mode(ImageMode.EnableDIY)
                it = iter(all_pixels)
                while chunk := list(islice(it, MAX_PIXEL_LIST_LENGTH)):
                    await client.graffiti.set_pixels(color, chunk)

            logger.info(
                "All %d pixels lit. Running %.1fs countdown (%.3fs/pixel)...",
                total_pixels,
                total_seconds,
                total_seconds / total_pixels,
            )
            start = time.monotonic()
            cleared = 0

            while cleared < total_pixels:
                next_target_time = start + ((cleared + 1) / total_pixels) * total_seconds
                now = time.monotonic()
                self.countdown_elapsed_seconds = min(total_seconds, now - start)
                sleep_duration = next_target_time - now
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)

                elapsed = time.monotonic() - start
                self.countdown_elapsed_seconds = min(total_seconds, elapsed)
                target_cleared = min(
                    total_pixels,
                    max(cleared + 1, int((elapsed / total_seconds) * total_pixels)),
                )
                to_clear = all_pixels[cleared:target_cleared]

                async with self._ble_lock:
                    client = await self.ensure_connected()
                    it_clear = iter(to_clear)
                    while chunk := list(islice(it_clear, MAX_PIXEL_LIST_LENGTH)):
                        await client.graffiti.set_pixels((0, 0, 0), chunk)

                cleared = target_cleared
                self.countdown_cleared_pixels = cleared
                lit = total_pixels - cleared
                rem = max(0.0, total_seconds - (time.monotonic() - start))
                mins, secs = divmod(int(round(rem)), 60)
                self.status_message = f"Countdown: {mins:02d}:{secs:02d} left ({lit}/{total_pixels} px lit)"

            self.countdown_elapsed_seconds = total_seconds
            self.countdown_running = False
            self.active_mode = "idle"
            self.status_message = "Countdown finished (0 pixels lit)"
            logger.info("Countdown complete!")
        except asyncio.CancelledError:
            logger.info("Countdown cancelled")
            raise
        except Exception as exc:
            logger.exception("Countdown failed")
            self.countdown_running = False
            self.status_message = f"Countdown error: {exc}"

    def get_state(self) -> dict[str, Any]:
        total_px = self.countdown_total_pixels or 1024
        cleared_px = self.countdown_cleared_pixels
        lit_px = max(0, total_px - cleared_px) if (self.countdown_running or cleared_px > 0) else 0
        rem_sec = max(0.0, self.countdown_total_seconds - self.countdown_elapsed_seconds)
        percent = (
            min(100.0, (self.countdown_elapsed_seconds / self.countdown_total_seconds) * 100.0)
            if self.countdown_total_seconds > 0
            else 0.0
        )
        return {
            "connected": self.is_connected,
            "device_address": self.mac_address,
            "screen_size": self.screen_size_label,
            "busy": self.busy,
            "active_mode": self.active_mode,
            "active_picture": self.active_picture,
            "status_message": self.status_message,
            "countdown": {
                "running": self.countdown_running,
                "total_seconds": self.countdown_total_seconds,
                "elapsed_seconds": round(self.countdown_elapsed_seconds, 1),
                "remaining_seconds": round(rem_sec, 1),
                "total_pixels": total_px,
                "cleared_pixels": cleared_px,
                "lit_pixels": lit_px,
                "percent": round(percent, 1),
                "color": self.countdown_color,
            },
            "library": ensure_library(),
        }


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>iDotMatrix Companion</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #4285f4;
      --green: #34a853;
      --red: #ea4335;
      --yellow: #fbbc05;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      padding: 24px 16px 48px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .container {
      max-width: 880px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    header {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 20px;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
    }
    .title-group h1 {
      margin: 0;
      font-size: 1.25rem;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .badge {
      font-size: 0.78rem;
      padding: 3px 10px;
      border-radius: 999px;
      font-weight: 600;
      background: #21262d;
      border: 1px solid var(--border);
    }
    .badge.online {
      background: rgba(52, 168, 83, 0.18);
      color: #56d364;
      border-color: rgba(52, 168, 83, 0.45);
    }
    .status-line {
      margin-top: 4px;
      font-size: 0.88rem;
      color: var(--muted);
    }
    .controls-row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
    }
    .card h2 {
      margin: 0 0 6px;
      font-size: 1.1rem;
    }
    .card p.sub {
      margin: 0 0 16px;
      font-size: 0.88rem;
      color: var(--muted);
    }
    .library-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
      gap: 16px;
    }
    .pic-card {
      background: #090d11;
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
      transition: border-color 0.15s, transform 0.15s;
    }
    .pic-card.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px var(--accent);
    }
    .pic-preview {
      width: 112px;
      height: 112px;
      background: #000;
      border-radius: 8px;
      border: 1px solid #222;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }
    .pic-preview img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      image-rendering: auto;
    }
    .pic-title {
      font-weight: 600;
      font-size: 0.95rem;
      text-align: center;
    }
    button {
      cursor: pointer;
      font: inherit;
      font-size: 0.88rem;
      font-weight: 600;
      padding: 8px 14px;
      border-radius: 8px;
      border: 1px solid var(--border);
      background: #21262d;
      color: var(--text);
      transition: background 0.15s, opacity 0.15s;
    }
    button:hover:not(:disabled) {
      background: #30363d;
    }
    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    button.primary:hover:not(:disabled) {
      background: #3367d6;
    }
    button.success {
      background: var(--green);
      border-color: var(--green);
      color: #fff;
    }
    button.danger {
      background: var(--red);
      border-color: var(--red);
      color: #fff;
    }
    .preset-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 14px;
    }
    .countdown-form {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: flex-end;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 4px;
      font-size: 0.82rem;
      color: var(--muted);
    }
    input[type="number"], input[type="text"] {
      background: #0d1117;
      border: 1px solid var(--border);
      color: var(--text);
      border-radius: 6px;
      padding: 7px 10px;
      width: 90px;
      font-size: 0.95rem;
    }
    input[type="color"] {
      background: #0d1117;
      border: 1px solid var(--border);
      border-radius: 6px;
      width: 48px;
      height: 35px;
      padding: 2px;
      cursor: pointer;
    }
    .progress-box {
      margin-top: 16px;
      padding: 14px;
      border-radius: 8px;
      background: #090d11;
      border: 1px solid var(--border);
    }
    .progress-header {
      display: flex;
      justify-content: space-between;
      font-size: 0.9rem;
      margin-bottom: 8px;
    }
    .progress-bar-outer {
      width: 100%;
      height: 12px;
      background: #21262d;
      border-radius: 999px;
      overflow: hidden;
    }
    .progress-bar-inner {
      height: 100%;
      width: 0%;
      background: var(--green);
      transition: width 0.25s linear;
    }
    .upload-row {
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
      font-size: 0.85rem;
      color: var(--muted);
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="title-group">
        <h1>
          iDotMatrix Companion
          <span id="connBadge" class="badge">Disconnected</span>
        </h1>
        <div id="statusText" class="status-line">Ready</div>
      </div>
      <div class="controls-row">
        <label style="font-size:0.84rem; color:var(--muted); display:flex; align-items:center; gap:6px;">
          Brightness
          <input id="brightnessSlider" type="range" min="5" max="100" value="80" onchange="setBrightness(this.value)" />
        </label>
        <button id="connectBtn" onclick="toggleConnection()">Connect</button>
      </div>
    </header>

    <section class="card">
      <h2>Picture Library (Black Screen)</h2>
      <p class="sub">Click a logo to display it immediately on your iDotMatrix panel with smooth anti-aliasing.</p>
      <div id="libraryGrid" class="library-grid"></div>
      <div class="upload-row">
        <span>Add custom image to library:</span>
        <div style="display:flex; gap:8px; align-items:center;">
          <input type="file" id="uploadInput" accept="image/*" style="font-size:0.82rem;" />
          <button onclick="uploadImage()">Add to Library</button>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>LED Pixel Countdown Timer</h2>
      <p class="sub">
        Lights up all pixels in DIY mode and turns them black one by one over the total time without showing any upload/link icon.
      </p>
      <div class="preset-row">
        <button onclick="setPreset(1, 0)">1 min</button>
        <button onclick="setPreset(2, 0)">2 min</button>
        <button onclick="setPreset(5, 0)">5 min</button>
        <button onclick="setPreset(15, 0)">15 min</button>
        <button onclick="setPreset(30, 0)">30 min</button>
        <button onclick="setPreset(60, 0)">60 min</button>
      </div>
      <div class="countdown-form">
        <div class="field">
          <label for="cdMinutes">Minutes</label>
          <input id="cdMinutes" type="number" min="0" max="999" value="60" />
        </div>
        <div class="field">
          <label for="cdSeconds">Seconds</label>
          <input id="cdSeconds" type="number" min="0" max="59" value="0" />
        </div>
        <div class="field">
          <label for="cdColor">Pixel Color</label>
          <input id="cdColor" type="color" value="#00ff00" />
        </div>
        <button class="success" onclick="startCountdown()">Start Countdown</button>
        <button id="stopCdBtn" class="danger" onclick="stopCountdown()" disabled>Stop</button>
      </div>

      <div class="progress-box">
        <div class="progress-header">
          <span id="cdTimeLabel">Idle</span>
          <span id="cdPixelLabel">0 / 1024 pixels lit</span>
        </div>
        <div class="progress-bar-outer">
          <div id="cdProgressBar" class="progress-bar-inner"></div>
        </div>
      </div>
    </section>
  </div>

  <script>
    let currentLibraryJson = "";

    function formatTime(sec) {
      sec = Math.max(0, Math.round(sec));
      const m = Math.floor(sec / 60);
      const s = sec % 60;
      return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    }

    async function apiPost(path, body = {}) {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.error || "Request failed");
      }
      await refreshStatus();
      return data;
    }

    async function showPicture(id) {
      await apiPost("/api/show-picture", { id });
    }

    function setPreset(mins, secs) {
      document.getElementById("cdMinutes").value = mins;
      document.getElementById("cdSeconds").value = secs;
    }

    async function startCountdown() {
      const mins = parseInt(document.getElementById("cdMinutes").value || "0", 10);
      const secs = parseInt(document.getElementById("cdSeconds").value || "0", 10);
      const color = document.getElementById("cdColor").value;
      const totalSeconds = mins * 60 + secs;
      if (totalSeconds <= 0) {
        alert("Please enter a duration greater than 0 seconds.");
        return;
      }
      await apiPost("/api/countdown/start", { total_seconds: totalSeconds, color });
    }

    async function stopCountdown() {
      await apiPost("/api/countdown/stop");
    }

    async function setBrightness(val) {
      await apiPost("/api/brightness", { brightness: parseInt(val, 10) });
    }

    async function toggleConnection() {
      const badge = document.getElementById("connBadge");
      if (badge.classList.contains("online")) {
        await apiPost("/api/disconnect");
      } else {
        await apiPost("/api/connect");
      }
    }

    async function uploadImage() {
      const input = document.getElementById("uploadInput");
      if (!input.files || !input.files[0]) {
        alert("Choose an image file first.");
        return;
      }
      const file = input.files[0];
      const reader = new FileReader();
      reader.onload = async () => {
        const base64Data = reader.result.split(",")[1];
        await apiPost("/api/upload", { filename: file.name, data_base64: base64Data });
        input.value = "";
      };
      reader.readAsDataURL(file);
    }

    function renderLibrary(library, activePic) {
      const key = JSON.stringify({ library, activePic });
      if (key === currentLibraryJson) return;
      currentLibraryJson = key;
      const grid = document.getElementById("libraryGrid");
      grid.innerHTML = library.map(item => `
        <div class="pic-card ${activePic === item.filename ? 'active' : ''}">
          <div class="pic-preview">
            <img src="/library/${encodeURIComponent(item.filename)}?v=${item.mtime || 1}" alt="${item.title}" />
          </div>
          <div class="pic-title">${item.title}</div>
          <button class="primary" style="width:100%" onclick="showPicture('${item.filename}')">
            Show on Display
          </button>
        </div>
      `).join("");
    }

    async function refreshStatus() {
      try {
        const res = await fetch("/api/status");
        const st = await res.json();

        const badge = document.getElementById("connBadge");
        const connBtn = document.getElementById("connectBtn");
        if (st.connected) {
          badge.textContent = `Connected (${st.screen_size})`;
          badge.classList.add("online");
          connBtn.textContent = "Disconnect";
        } else {
          badge.textContent = "Disconnected";
          badge.classList.remove("online");
          connBtn.textContent = "Connect";
        }

        document.getElementById("statusText").textContent = st.status_message;
        renderLibrary(st.library || [], st.active_mode === "picture" ? st.active_picture : null);

        const cd = st.countdown;
        document.getElementById("stopCdBtn").disabled = !cd.running;
        const bar = document.getElementById("cdProgressBar");
        bar.style.background = cd.color || "#34a853";

        if (cd.running || cd.elapsed_seconds > 0) {
          const remainingRatio = cd.total_seconds > 0 ? Math.max(0, 100 - cd.percent) : 0;
          bar.style.width = remainingRatio + "%";
          document.getElementById("cdTimeLabel").textContent =
            cd.running
              ? `Remaining: ${formatTime(cd.remaining_seconds)} / ${formatTime(cd.total_seconds)}`
              : `Finished (${formatTime(cd.total_seconds)})`;
          document.getElementById("cdPixelLabel").textContent =
            `${cd.lit_pixels} / ${cd.total_pixels} pixels lit (${(100 - cd.percent).toFixed(0)}%)`;
        } else {
          bar.style.width = "0%";
          document.getElementById("cdTimeLabel").textContent = "Idle";
          document.getElementById("cdPixelLabel").textContent = `0 / ${cd.total_pixels} pixels lit`;
        }
      } catch (e) {
        console.error(e);
      }
    }

    refreshStatus();
    setInterval(refreshStatus, 1000);
  </script>
</body>
</html>
"""


class WebServer:
    """Minimal asyncio HTTP server so BLE and Web UI share a single event loop with zero extra dependencies."""

    def __init__(self, controller: CompanionController) -> None:
        self.controller = controller

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not request_line:
                return
            parts = request_line.decode("utf-8", errors="replace").strip().split(" ")
            if len(parts) < 2:
                return
            method, raw_path = parts[0], parts[1]
            path = unquote(raw_path.split("?", 1)[0])

            headers: dict[str, str] = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if not line or line in (b"\r\n", b"\n"):
                    break
                decoded = line.decode("utf-8", errors="replace").strip()
                if ":" in decoded:
                    k, v = decoded.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            content_length = int(headers.get("content-length", "0"))
            body_bytes = await asyncio.wait_for(reader.readexactly(content_length), timeout=10.0) if content_length > 0 else b""

            status, content_type, payload = await self.route(method, path, body_bytes)
            await self._send_response(writer, status, content_type, payload)
        except Exception as exc:
            logger.debug("HTTP handler error: %s", exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def route(self, method: str, path: str, body: bytes) -> tuple[str, str, bytes]:
        if method == "GET" and path == "/":
            return "200 OK", "text/html; charset=utf-8", INDEX_HTML.encode("utf-8")

        if method == "GET" and path == "/api/status":
            return self._json_response("200 OK", self.controller.get_state())

        if method == "GET" and path.startswith("/library/"):
            filename = Path(path.removeprefix("/library/")).name
            file_path = (LIBRARY_DIR / filename).resolve()
            if file_path.exists() and file_path.parent == LIBRARY_DIR.resolve():
                mime, _ = mimetypes.guess_type(str(file_path))
                return "200 OK", mime or "image/png", file_path.read_bytes()
            return self._json_response("404 Not Found", {"error": "File not found"})

        if method == "POST":
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                data = {}

            try:
                if path == "/api/connect":
                    await self.controller.ensure_connected()
                    return self._json_response("200 OK", self.controller.get_state())

                if path == "/api/disconnect":
                    await self.controller.disconnect()
                    return self._json_response("200 OK", self.controller.get_state())

                if path == "/api/brightness":
                    await self.controller.set_brightness(int(data.get("brightness", 80)))
                    return self._json_response("200 OK", self.controller.get_state())

                if path == "/api/show-picture":
                    pic_id = data.get("id", "")
                    await self.controller.show_picture(pic_id)
                    return self._json_response("200 OK", self.controller.get_state())

                if path == "/api/countdown/start":
                    total_seconds = float(data.get("total_seconds", 60))
                    color = str(data.get("color", "#00ff00"))
                    await self.controller.start_countdown(total_seconds=total_seconds, color_hex=color)
                    return self._json_response("200 OK", self.controller.get_state())

                if path == "/api/countdown/stop":
                    await self.controller.stop_countdown()
                    return self._json_response("200 OK", self.controller.get_state())

                if path == "/api/upload":
                    filename = Path(data.get("filename", "custom.png")).stem + ".png"
                    raw = base64.b64decode(data.get("data_base64", ""))
                    with Image.open(io.BytesIO(raw)) as uploaded:
                        rgba = uploaded.convert("RGBA")
                        bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
                        composited = Image.alpha_composite(bg, rgba).convert("RGB")
                        composited.save(LIBRARY_DIR / filename, "PNG")
                    return self._json_response("200 OK", self.controller.get_state())
            except Exception as exc:
                logger.exception("API error on %s", path)
                return self._json_response("400 Bad Request", {"error": str(exc)})

        return self._json_response("404 Not Found", {"error": "Not found"})

    @staticmethod
    def _json_response(status: str, obj: dict[str, Any]) -> tuple[str, str, bytes]:
        return status, "application/json; charset=utf-8", json.dumps(obj).encode("utf-8")

    @staticmethod
    async def _send_response(
        writer: asyncio.StreamWriter, status: str, content_type: str, payload: bytes
    ) -> None:
        header = (
            f"HTTP/1.1 {status}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: close\r\n\r\n"
        ).encode("utf-8")
        writer.write(header + payload)
        await writer.drain()


async def async_main(host: str, port: int, mac_address: str | None) -> None:
    # Ensure predefined logos exist in the library on a black background
    ensure_library()

    controller = CompanionController(mac_address=mac_address)
    web = WebServer(controller)
    server = await asyncio.start_server(web.handle_client, host, port)
    logger.info("iDotMatrix Companion running at http://%s:%d", host, port)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="iDotMatrix Companion Web App")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument(
        "--mac",
        default=None,
        help="Optional BLE address/UUID of the iDotMatrix device (default: auto-discover)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(async_main(args.host, args.port, args.mac))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
