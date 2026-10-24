#!/usr/bin/env python3
"""CLI helper for the idotmatrix-display skill.

Automatically routes commands through the running iDotMatrix Companion HTTP server
(http://127.0.0.1:8080) when available, or falls back to direct BLE communication
via idotmatrix-api-client.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import deque
import io
from itertools import islice
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import urllib.error
import urllib.request

from PIL import Image

from idotmatrix.client import IDotMatrixClient
from idotmatrix.modules.graffiti import MAX_PIXEL_LIST_LENGTH
from idotmatrix.modules.image import ImageMode
from idotmatrix.screensize import ScreenSize
from idotmatrix.util import color_utils

DEFAULT_SERVER_URL = "http://127.0.0.1:8080"
DEFAULT_MAC_ADDRESS = os.environ.get("IDOTMATRIX_MAC")
COMPANION_DIR = Path(__file__).resolve().parents[4]
LIBRARY_DIR = COMPANION_DIR / "library"

PICTURE_ALIASES: dict[str, str] = {
    "google-cloud": "google-cloud.png",
    "google-cloud.png": "google-cloud.png",
    "cloud": "google-cloud.png",
    "gcp": "google-cloud.png",
    "google-g": "google-g.png",
    "google-g.png": "google-g.png",
    "g": "google-g.png",
    "google": "google-g.png",
    "antigravity": "antigravity.png",
    "antigravity.png": "antigravity.png",
    "agy": "antigravity.png",
    "red-heart": "red-heart.png",
    "red-heart.png": "red-heart.png",
    "heart": "red-heart.png",
    "thumbs-up": "thumbs-up.png",
    "thumbs-up.png": "thumbs-up.png",
    "thumbsup": "thumbs-up.png",
    "thumb": "thumbs-up.png",
    "like": "thumbs-up.png",
}


def http_request(
    server_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Send a JSON request to the Companion HTTP server, returning None if unreachable."""
    url = f"{server_url.rstrip('/')}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Companion API error ({exc.code}): {body}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None


def prepare_black_background_png(source_path: Path, strip_white_bg: bool = False, size: int = 512) -> bytes:
    """Load an image, composite onto pure black (0, 0, 0), optionally strip perimeter white card."""
    with Image.open(source_path) as img:
        rgba = img.convert("RGBA")
        w, h = rgba.size
        px = rgba.load()
        out = Image.new("RGB", (w, h), (0, 0, 0))
        out_px = out.load()

        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a < 30:
                    out_px[x, y] = (0, 0, 0)
                else:
                    out_px[x, y] = (
                        int(r * (a / 255.0)),
                        int(g * (a / 255.0)),
                        int(b * (a / 255.0)),
                    )

        if strip_white_bg:
            visited = bytearray(w * h)
            queue: deque[tuple[int, int]] = deque()
            for x in range(w):
                queue.append((x, 0))
                queue.append((x, h - 1))
            for y in range(h):
                queue.append((0, y))
                queue.append((w - 1, y))

            while queue:
                cx, cy = queue.popleft()
                idx = cy * w + cx
                if visited[idx]:
                    continue
                visited[idx] = 1
                r, g, b = out_px[cx, cy]
                if (max(r, g, b) - min(r, g, b)) < 35 or max(r, g, b) < 40:
                    out_px[cx, cy] = (0, 0, 0)
                    if cx > 0:
                        queue.append((cx - 1, cy))
                    if cx + 1 < w:
                        queue.append((cx + 1, cy))
                    if cy > 0:
                        queue.append((cx, cy - 1))
                    if cy + 1 < h:
                        queue.append((cx, cy + 1))

        bbox = out.getbbox()
        if bbox:
            out = out.crop(bbox)
        canvas = Image.new("RGB", (size, size), (0, 0, 0))
        target_w = int(size * 0.88)
        ratio = min(target_w / max(1, out.width), target_w / max(1, out.height))
        resized = out.resize(
            (max(1, round(out.width * ratio)), max(1, round(out.height * ratio))),
            Image.Resampling.LANCZOS,
        )
        canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()


async def direct_show_picture(mac: str, file_path: Path) -> dict[str, Any]:
    """Connect directly via BLE and display an image file on a pure black background."""
    client = IDotMatrixClient(
        screen_size=ScreenSize.SIZE_32x32,
        mac_address=mac,
        install_signal_handlers=False,
    )
    await client.connect()
    try:
        await client.image.show_image_file(
            file_path=file_path,
            background_color=(0, 0, 0),
        )
        return {
            "transport": "direct-ble",
            "connected": True,
            "active_mode": "picture",
            "active_picture": file_path.name,
            "status_message": f"Displayed {file_path.name} via direct BLE",
        }
    finally:
        await client.disconnect()


async def direct_run_countdown(mac: str, total_seconds: float, color_hex: str) -> dict[str, Any]:
    """Connect directly via BLE and run the full-screen DIY pixel countdown timer."""
    color_rgb = color_utils.parse_color_rgb(color_hex)
    client = IDotMatrixClient(
        screen_size=ScreenSize.SIZE_32x32,
        mac_address=mac,
        install_signal_handlers=False,
    )
    await client.connect()
    try:
        width, height = client.screen_size.value
        all_pixels = [(x, y) for y in range(height) for x in range(width)]
        total_pixels = len(all_pixels)

        await client.image.set_mode(ImageMode.EnableDIY)
        it = iter(all_pixels)
        while chunk := list(islice(it, MAX_PIXEL_LIST_LENGTH)):
            await client.graffiti.set_pixels(color_rgb, chunk)

        start = time.monotonic()
        cleared = 0
        while cleared < total_pixels:
            next_target_time = start + ((cleared + 1) / total_pixels) * total_seconds
            now = time.monotonic()
            sleep_duration = next_target_time - now
            if sleep_duration > 0:
                await asyncio.sleep(sleep_duration)

            elapsed = time.monotonic() - start
            target_cleared = min(
                total_pixels,
                max(cleared + 1, int((elapsed / total_seconds) * total_pixels)),
            )
            to_clear = all_pixels[cleared:target_cleared]
            it_clear = iter(to_clear)
            while chunk := list(islice(it_clear, MAX_PIXEL_LIST_LENGTH)):
                await client.graffiti.set_pixels((0, 0, 0), chunk)
            cleared = target_cleared

        return {
            "transport": "direct-ble",
            "connected": True,
            "active_mode": "idle",
            "countdown": {
                "running": False,
                "total_seconds": total_seconds,
                "cleared_pixels": total_pixels,
                "lit_pixels": 0,
                "percent": 100.0,
            },
            "status_message": "Countdown finished (0 pixels lit)",
        }
    finally:
        await client.disconnect()


async def direct_set_brightness(mac: str, brightness: int) -> dict[str, Any]:
    """Connect directly via BLE and set screen brightness."""
    brightness = max(5, min(100, int(brightness)))
    client = IDotMatrixClient(
        screen_size=ScreenSize.SIZE_32x32,
        mac_address=mac,
        install_signal_handlers=False,
    )
    await client.connect()
    try:
        await client.set_brightness(brightness)
        return {
            "transport": "direct-ble",
            "brightness": brightness,
            "status_message": f"Brightness set to {brightness}%",
        }
    finally:
        await client.disconnect()


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    if not args.direct:
        state = http_request(args.server, "GET", "/api/status", timeout=5.0)
        if state is not None:
            state["transport"] = "companion-http"
            return state
    files = sorted(p.name for p in LIBRARY_DIR.glob("*.png")) if LIBRARY_DIR.exists() else []
    return {
        "transport": "direct-ble",
        "companion_server_running": False,
        "device_address": args.mac,
        "library_files": files,
    }


def cmd_picture(args: argparse.Namespace) -> dict[str, Any]:
    target = args.target.strip()
    alias_key = target.lower()

    if alias_key in PICTURE_ALIASES:
        filename = PICTURE_ALIASES[alias_key]
        file_path = LIBRARY_DIR / filename
        if not file_path.exists():
            raise FileNotFoundError(f"Library image not found at {file_path}")

        if not args.direct:
            state = http_request(args.server, "POST", "/api/show-picture", {"id": filename})
            if state is not None:
                state["transport"] = "companion-http"
                return state

        return asyncio.run(direct_show_picture(args.mac, file_path))

    # Check if target exists inside LIBRARY_DIR
    candidate_in_lib = LIBRARY_DIR / Path(target).name
    if candidate_in_lib.exists() and not Path(target).is_absolute():
        if not args.direct:
            state = http_request(args.server, "POST", "/api/show-picture", {"id": candidate_in_lib.name})
            if state is not None:
                state["transport"] = "companion-http"
                return state
        return asyncio.run(direct_show_picture(args.mac, candidate_in_lib))

    # Otherwise treat target as an arbitrary image file path
    source_path = Path(target).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(
            f"Image '{target}' not found. Built-in logos: google-cloud, google-g, antigravity."
        )

    png_bytes = prepare_black_background_png(source_path, strip_white_bg=args.strip_white_bg)
    dest_filename = f"{source_path.stem}.png"
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = LIBRARY_DIR / dest_filename
    dest_path.write_bytes(png_bytes)

    if not args.direct:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        uploaded = http_request(
            args.server,
            "POST",
            "/api/upload",
            {"filename": dest_filename, "data_base64": b64},
        )
        if uploaded is not None:
            state = http_request(args.server, "POST", "/api/show-picture", {"id": dest_filename})
            if state is not None:
                state["transport"] = "companion-http"
                return state

    return asyncio.run(direct_show_picture(args.mac, dest_path))


def cmd_countdown(args: argparse.Namespace) -> dict[str, Any]:
    if not args.direct:
        state = http_request(
            args.server,
            "POST",
            "/api/countdown/start",
            {"total_seconds": args.seconds, "color": args.color},
        )
        if state is not None:
            state["transport"] = "companion-http"
            if args.wait:
                while True:
                    time.sleep(1.0)
                    polled = http_request(args.server, "GET", "/api/status", timeout=5.0)
                    if polled is None or not polled.get("countdown", {}).get("running", False):
                        if polled is not None:
                            polled["transport"] = "companion-http"
                            return polled
                        break
            return state

    return asyncio.run(direct_run_countdown(args.mac, args.seconds, args.color))


def cmd_stop(args: argparse.Namespace) -> dict[str, Any]:
    state = http_request(args.server, "POST", "/api/countdown/stop", {})
    if state is not None:
        state["transport"] = "companion-http"
        return state
    return {
        "transport": "direct-ble",
        "status_message": "Companion server is not running; no background countdown to stop.",
    }


def cmd_brightness(args: argparse.Namespace) -> dict[str, Any]:
    if not args.direct:
        state = http_request(args.server, "POST", "/api/brightness", {"brightness": args.level})
        if state is not None:
            state["transport"] = "companion-http"
            return state
    return asyncio.run(direct_set_brightness(args.mac, args.level))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the iDotMatrix 32x32 LED display.")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help="Companion HTTP server URL")
    parser.add_argument("--mac", default=DEFAULT_MAC_ADDRESS, help="BLE UUID / MAC address")
    parser.add_argument("--direct", action="store_true", help="Force direct BLE instead of Companion HTTP API")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Get current display and countdown status")

    pic_p = sub.add_parser("picture", help="Display a built-in logo (google-cloud, google-g, antigravity) or image file")
    pic_p.add_argument("target", help="Logo alias (google-cloud | google-g | antigravity) or path to an image file")
    pic_p.add_argument(
        "--strip-white-bg",
        action="store_true",
        help="Flood-fill white/gray perimeter background to pure black (0, 0, 0)",
    )

    cd_p = sub.add_parser("countdown", help="Start the full-screen 1,024-pixel LED countdown timer")
    cd_p.add_argument("--seconds", type=float, required=True, help="Countdown duration in seconds")
    cd_p.add_argument("--color", default="#00ff00", help="Hex color for lit pixels (default: #00ff00)")
    cd_p.add_argument("--wait", action="store_true", help="Wait for countdown completion before returning")

    sub.add_parser("stop", help="Stop an active countdown timer")

    br_p = sub.add_parser("brightness", help="Set display brightness (5-100)")
    br_p.add_argument("level", type=int, help="Brightness percentage (5-100)")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dispatch = {
        "status": cmd_status,
        "picture": cmd_picture,
        "countdown": cmd_countdown,
        "stop": cmd_stop,
        "brightness": cmd_brightness,
    }
    try:
        result = dispatch[args.command](args)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
