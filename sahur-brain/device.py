"""
device.py — thin client for the on-device device control server control server.

device control server (https://github.com/witchan/device control server) is a rootless SpringBoard tweak that
runs an HTTP JSON-RPC server on the phone (default :8090). It is the "hands and
eyes" of Sahur: tap, swipe, type, screenshot, read the accessibility tree, launch
apps, open URL schemes. We just call it over the LAN.

Every tool is invoked as:
    POST http://<phone-ip>:8090/mcp
    {"jsonrpc":"2.0","id":<n>,"method":"tools/call",
     "params":{"name":"<tool>","arguments":{...}}}

The response is standard MCP: result.content[] is a list of {"type":"text"|"image",...}.
We unwrap text content (JSON-decoding it when possible) into plain Python.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


class DeviceError(RuntimeError):
    pass


@dataclass
class UIElement:
    """A flattened, tappable accessibility node (device control server schema)."""

    label: str
    role: str
    value: str
    identifier: str
    x: float
    y: float
    width: float
    height: float
    enabled: bool
    tap_x: float | None = None  # exact tap point device control server recommends
    tap_y: float | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def center(self) -> tuple[int, int]:
        if self.tap_x is not None and self.tap_y is not None:
            return int(self.tap_x), int(self.tap_y)
        return int(self.x + self.width / 2), int(self.y + self.height / 2)

    def describe(self) -> str:
        bits = [f'"{self.label}"' if self.label else "(no-label)"]
        if self.role:
            bits.append(f"<{self.role}>")
        if self.value:
            bits.append(f"={self.value!r}")
        if self.identifier:
            bits.append(f"id={self.identifier}")
        cx, cy = self.center
        bits.append(f"@({cx},{cy})")
        if not self.enabled:
            bits.append("[disabled]")
        return " ".join(bits)


class DeviceClient:
    def __init__(self, base_url: str | None = None, timeout: float = 60.0):
        base_url = base_url or os.environ.get("DEVICE_BASE_URL", "http://127.0.0.1:8090")
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)
        self._ids = itertools.count(1)
        # On-screen Sahur sprite: before each tap, tell the SpringBoard overlay to
        # walk to the target and poke it (then we fire the real tap).
        self._visual = os.environ.get("SAHUR_VISUAL", "0") == "1"
        self._tap_file = os.environ.get("SAHUR_TAP_FILE", "/var/mobile/Library/Caches/sahur_tap.txt")
        self._lead_ms = int(os.environ.get("SAHUR_WALK_LEAD_MS", "650"))
        self._tapseq = 0

    # ---- transport ---------------------------------------------------------

    def _reset_client(self) -> None:
        """Drop the (possibly poisoned) connection pool and start fresh. iproxy/USB
        resets leave dead keep-alive sockets that fail every reuse until recycled."""
        try:
            self._client.close()
        except Exception:
            pass
        self._client = httpx.Client(timeout=self._timeout)

    def _request(self, method: str, url: str, *, retries: int = 3, **kw):
        """HTTP with self-healing: on a transport reset (Errno 54 / dropped USB
        connection) recreate the client and retry, so one iproxy hiccup doesn't
        abort an action mid-sequence."""
        last = None
        for attempt in range(retries):
            try:
                return self._client.request(method, url, **kw)
            except (httpx.TransportError, httpx.RemoteProtocolError) as e:
                last = e
                self._reset_client()
                time.sleep(0.3 * (attempt + 1))
        raise last

    def health(self) -> dict:
        r = self._request("GET", f"{self.base_url}/health")
        r.raise_for_status()
        return r.json()

    def call(self, name: str, arguments: dict | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        r = self._request("POST", f"{self.base_url}/mcp", json=payload)
        r.raise_for_status()
        body = r.json()
        if "error" in body and body["error"]:
            raise DeviceError(f"{name}: {body['error']}")
        result = body.get("result", {})
        if result.get("isError"):
            raise DeviceError(f"{name}: {_text_of(result)}")
        return _unwrap(result)

    def tools_list(self) -> Any:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": "tools/list", "params": {}}
        r = self._request("POST", f"{self.base_url}/mcp", json=payload)
        r.raise_for_status()
        return r.json().get("result", {})

    # ---- on-screen sprite signal ------------------------------------------

    def _signal_walk(self, x: int, y: int) -> None:
        """Tell the SpringBoard overlay to walk Sahur to (x,y) to SHOW where a tap
        happened. Called AFTER the real tap so his window never covers the target at
        tap time (covering it makes the injected tap hit his window, not the app).
        No-op unless SAHUR_VISUAL=1."""
        if not self._visual:
            return
        self._tapseq += 1
        cmd = f'printf "%s %s %s" {int(x)} {int(y)} {self._tapseq} > {self._tap_file}'
        try:
            self.run_command(cmd, timeout=5)
        except Exception:
            pass

    # ---- high-level tools --------------------------------------------------

    def tap(self, x: int, y: int) -> Any:
        # Sahur walks to BESIDE the target first (his window stays off the hitbox),
        # then the real tap fires onto the clear target.
        self._signal_walk(x, y)
        if self._visual:
            time.sleep(self._lead_ms / 1000.0)
        return self.call("tap_screen", {"x": x, "y": y})

    def double_tap(self, x: int, y: int) -> Any:
        self._signal_walk(x, y)
        if self._visual:
            time.sleep(self._lead_ms / 1000.0)
        return self.call("double_tap", {"x": x, "y": y})

    def long_press(self, x: int, y: int, duration_ms: int = 500) -> Any:
        self._signal_walk(x, y)
        if self._visual:
            time.sleep(self._lead_ms / 1000.0)
        return self.call("long_press", {"x": x, "y": y, "duration": duration_ms})

    def swipe(self, fx: int, fy: int, tx: int, ty: int, duration_ms: int = 300, steps: int = 20) -> Any:
        return self.call(
            "swipe_screen",
            {"fromX": fx, "fromY": fy, "toX": tx, "toY": ty, "duration": duration_ms, "steps": steps},
        )

    def open_url(self, url: str) -> Any:
        return self.call("open_url", {"url": url})

    def launch_app(self, bundle_id: str) -> Any:
        return self.call("launch_app", {"bundle_id": bundle_id})

    def kill_app(self, bundle_id: str) -> Any:
        return self.call("kill_app", {"bundle_id": bundle_id})

    def frontmost_app(self) -> Any:
        return self.call("get_frontmost_app", {})

    def list_apps(self, kind: str = "user") -> Any:
        return self.call("list_apps", {"type": kind})

    def screen_info(self) -> Any:
        return self.call("get_screen_info", {})

    def input_text(self, text: str) -> Any:
        return self.call("input_text", {"text": text})

    def press_key(self, key: str) -> Any:
        return self.call("press_key", {"key": key})

    def press_home(self) -> Any:
        return self.call("press_home", {})

    def screenshot_b64(self) -> str:
        """Return a base64 JPEG of the current screen (data only, no prefix)."""
        result = self.call("screenshot", {})
        if isinstance(result, dict):
            for key in ("image", "data", "base64", "screenshot"):
                if key in result:
                    return result[key]
        if isinstance(result, str):
            return result
        raise DeviceError(f"screenshot: unexpected result shape {type(result)}")

    def ui_elements(self, max_depth: int = 20, max_elements: int = 2000) -> list[UIElement]:
        raw = self.call("get_ui_elements", {"max_depth": max_depth, "max_elements": max_elements})
        return _flatten_elements(raw)

    def element_at(self, x: int, y: int) -> Any:
        return self.call("get_element_at_point", {"x": x, "y": y})

    def run_command(self, command: str, timeout: int = 10) -> Any:
        return self.call("run_command", {"command": command, "timeout": timeout})

    def close(self):
        self._client.close()


# ---- response helpers ------------------------------------------------------


def _text_of(result: dict) -> str:
    parts = []
    for item in result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


def _unwrap(result: dict) -> Any:
    """Pull the meaningful payload out of an MCP tool result.

    Prefers JSON-decoding the first text block; falls back to raw text, then to
    the whole result (e.g. for image content)."""
    if not isinstance(result, dict):
        return result
    content = result.get("content")
    if isinstance(content, list):
        # image content -> return the structured dict so callers can find b64
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image":
                return item
        text = _text_of(result)
        if text:
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                return text
    # Some builds put data directly under structuredContent / result keys.
    for key in ("structuredContent", "data", "value"):
        if key in result:
            return result[key]
    return result


def _coerce_num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _flatten_elements(raw: Any) -> list[UIElement]:
    """Parse device control server's get_ui_elements response into a flat list.

    device control server returns an already-flat list under 'elements', each like:
        {"index":0,"text":"WLAN","type":"control","clickable":true,
         "rect":{"x","y","width","height"}, "visible_rect":{...}, "tap":{"x","y"}}
    We also keep a tolerant fallback for nested/legacy shapes (label/frame/children)."""
    out: list[UIElement] = []

    nodes = raw
    if isinstance(raw, dict):
        for key in ("elements", "children", "tree", "root", "result", "ui_elements"):
            if key in raw and isinstance(raw[key], (list, dict)):
                nodes = raw[key]
                break
        else:
            nodes = [raw]
    if isinstance(nodes, dict):
        nodes = [nodes]
    if not isinstance(nodes, list):
        return out

    def rect_of(node: dict) -> tuple[float, float, float, float]:
        fr = node.get("rect") or node.get("visible_rect") or node.get("frame") or node
        gx = fr.get("x", fr.get("X")); gy = fr.get("y", fr.get("Y"))
        gw = fr.get("width", fr.get("Width")); gh = fr.get("height", fr.get("Height"))
        return _coerce_num(gx), _coerce_num(gy), _coerce_num(gw), _coerce_num(gh)

    def visit(node: Any):
        if isinstance(node, list):
            for n in node:
                visit(n)
            return
        if not isinstance(node, dict):
            return
        x, y, w, h = rect_of(node)
        label = str(node.get("text") or node.get("label") or node.get("interactive_label")
                    or node.get("description") or "").strip()
        role = str(node.get("type") or node.get("elementType") or node.get("role") or "").strip()
        value = str(node.get("value") or node.get("placeholder") or "").strip()
        ident = str(node.get("identifier") or node.get("id") or node.get("element_id") or "").strip()
        clickable = node.get("clickable")
        enabled = bool(clickable) if clickable is not None else bool(
            node.get("enabled", not node.get("disabled", False)))
        tap = node.get("tap") if isinstance(node.get("tap"), dict) else None
        tap_x = _coerce_num(tap.get("x")) if tap else None
        tap_y = _coerce_num(tap.get("y")) if tap else None
        if (w > 0 or h > 0 or tap) and (label or value or ident or role):
            out.append(UIElement(
                label=label, role=role, value=value, identifier=ident,
                x=x, y=y, width=w, height=h, enabled=enabled,
                tap_x=tap_x, tap_y=tap_y, raw=node,
            ))
        for ch in node.get("children") or []:
            visit(ch)

    visit(nodes)
    return out
