"""HTTP client for the Unreal Engine 5 scene API.

Communicates with the real UE5 SceneCommandServer (C++ HTTP module running
inside the editor).
"""

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)


class UnrealBridge:
    def __init__(self, base_url: str | None = None, timeout: int = 30):
        self.base_url = base_url or os.getenv("UE_API_URL", "http://localhost:8000")
        self.timeout = timeout
        self._s = requests.Session()

    def _req(self, method: str, path: str, body: Any = None, **kw) -> dict:
        url = f"{self.base_url}{path}"
        try:
            r = self._s.request(method, url, json=body, timeout=self.timeout, **kw)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.error("UE5 %s %s → %s", method, path, e)
            return {"success": False, "error": str(e)}

    # ── health ─────────────────────────────────────────────────────────────
    def health(self) -> dict:
        return self._req("GET", "/api/health")

    def get_scene_info(self) -> dict:
        return self._req("GET", "/api/scene/info")

    # ── level ──────────────────────────────────────────────────────────────
    def load_level(self, level: str) -> dict:
        return self._req("POST", "/api/scene/level", {"level": level})

    # ── weather ────────────────────────────────────────────────────────────
    def set_weather(self, preset: str | None = None, params: dict | None = None) -> dict:
        d: dict = {}
        if preset: d["preset"] = preset
        if params: d["params"] = params
        return self._req("POST", "/api/scene/weather", d)

    # ── camera ─────────────────────────────────────────────────────────────
    def set_camera(self, location: list, rotation: list | None = None, fov: float = 90) -> dict:
        d: dict = {"location": location, "fov": fov}
        if rotation: d["rotation"] = rotation
        return self._req("POST", "/api/scene/camera", d)

    def move_camera(self, command: str, **kw) -> dict:
        return self._req("POST", "/api/scene/camera/move", {"command": command, **kw})

    # ── actors ─────────────────────────────────────────────────────────────
    def spawn_actor(self, asset: str, location: list, rotation: list | None = None,
                    scale: list | None = None, name: str | None = None,
                    properties: dict | None = None) -> dict:
        d: dict = {"asset": asset, "location": location}
        if rotation: d["rotation"] = rotation
        if scale: d["scale"] = scale
        if name: d["name"] = name
        if properties: d["properties"] = properties
        return self._req("POST", "/api/scene/actors", d)

    def destroy_actor(self, actor_id: str) -> dict:
        return self._req("DELETE", f"/api/scene/actors/{actor_id}")

    def get_actors(self) -> dict:
        return self._req("GET", "/api/scene/actors")

    def clear_scene(self) -> dict:
        return self._req("POST", "/api/scene/clear")

    # ── screenshots ────────────────────────────────────────────────────────
    def capture_screenshot(self, filename: str, width: int = 1920, height: int = 1080,
                           camera_location: list | None = None,
                           camera_rotation: list | None = None) -> dict:
        d: dict = {"filename": filename, "width": width, "height": height}
        if camera_location: d["camera_location"] = camera_location
        if camera_rotation: d["camera_rotation"] = camera_rotation
        return self._req("POST", "/api/scene/screenshot", d)

    # ── batch ──────────────────────────────────────────────────────────────
    def execute_commands(self, commands: list[dict]) -> dict:
        return self._req("POST", "/api/scene/execute", {"commands": commands})

    # ── player ────────────────────────────────────────────────────────────
    def spawn_player(self, location: list | None = None, yaw: float = 0) -> dict:
        d: dict = {"yaw": yaw}
        if location: d["location"] = location
        return self._req("POST", "/api/player/spawn", d)

    def move_player(self, command: str, **kw) -> dict:
        return self._req("POST", "/api/player/move", {"command": command, **kw})

    def get_player_info(self) -> dict:
        return self._req("GET", "/api/player/info")

    # ── urls ───────────────────────────────────────────────────────────────
    @property
    def stream_url(self) -> str:
        return f"{self.base_url}/api/stream"

    @property
    def snapshot_url(self) -> str:
        return f"{self.base_url}/api/snapshot"
