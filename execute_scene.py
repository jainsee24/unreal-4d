#!/usr/bin/env python3
"""Execute scene commands from a JSON file against the UE5 scene server.

Usage:
    python execute_scene.py [commands_file] [renders_dir]

The script reads scene_commands.json, sends each command to the UE5 scene
API, and writes render_result.json with the outcome.
"""

import json
import logging
import sys
import time
from pathlib import Path

from ue_bridge import UnrealBridge

log = logging.getLogger(__name__)


def _exec_one(bridge: UnrealBridge, cmd: dict, renders_dir: str) -> dict:
    """Dispatch a single scene command."""
    t = cmd.get("type", "")

    if t == "load_level":
        return bridge.load_level(cmd["level"])

    if t == "set_weather":
        return bridge.set_weather(
            preset=cmd.get("preset"), params=cmd.get("params")
        )

    if t == "set_camera":
        return bridge.set_camera(
            location=cmd["location"],
            rotation=cmd.get("rotation"),
            fov=cmd.get("fov", 90),
        )

    if t in ("spawn_actor", "spawn_blueprint", "spawn_static_mesh"):
        return bridge.spawn_actor(
            asset=cmd["asset"],
            location=cmd["location"],
            rotation=cmd.get("rotation"),
            scale=cmd.get("scale"),
            name=cmd.get("name"),
            properties=cmd.get("properties"),
        )

    if t == "destroy_actor":
        return bridge.destroy_actor(cmd["actor_id"])

    if t == "clear_scene":
        return bridge.clear_scene()

    if t == "capture_screenshot":
        return bridge.capture_screenshot(
            filename=cmd["filename"],
            width=cmd.get("width", 1920),
            height=cmd.get("height", 1080),
            camera_location=cmd.get("camera_location"),
            camera_rotation=cmd.get("camera_rotation"),
        )

    if t == "wait":
        time.sleep(cmd.get("seconds", 1.0))
        return {"success": True}

    return {"success": False, "error": f"Unknown command type: {t}"}


def execute_scene(commands_file: str, renders_dir: str = "renders") -> dict:
    """Run every command in *commands_file* and return an aggregate result.

    Uses batch execution (/api/scene/execute) when possible for much faster
    scene building (one HTTP request vs hundreds).
    """
    bridge = UnrealBridge()

    path = Path(commands_file)
    if not path.exists():
        return {"success": False, "error": f"File not found: {commands_file}"}

    data = json.loads(path.read_text())
    commands = data.get("commands", [])

    if not commands:
        return {"success": True, "actors_spawned": 0, "actors_failed": 0,
                "images": [], "errors": [], "command_results": []}

    # Save current camera so we can restore it after screenshots
    saved_camera = None
    try:
        info = bridge.get_scene_info()
        if info.get("success") and info.get("camera"):
            saved_camera = info["camera"]
    except Exception:
        pass

    log.info("Executing %d commands (batch mode)...", len(commands))

    # Try batch execution first (much faster: 1 request instead of N)
    try:
        batch_result = bridge.execute_commands(commands)
        if batch_result.get("success") is not None:
            # Batch execute worked — extract results
            result = {
                "success": batch_result.get("success", True),
                "actors_spawned": batch_result.get("actors_spawned", 0),
                "actors_failed": batch_result.get("actors_failed", 0),
                "images": batch_result.get("images", []),
                "errors": batch_result.get("errors", []),
                "command_results": batch_result.get("command_results", []),
            }
            log.info("Batch execute: %d spawned, %d failed",
                     result["actors_spawned"], result["actors_failed"])

            # Restore camera
            if saved_camera:
                try:
                    bridge.set_camera(
                        location=saved_camera.get("location", [0, 0, 500]),
                        rotation=saved_camera.get("rotation", [-30, 0, 0]),
                    )
                except Exception:
                    pass
            return result
    except Exception as e:
        log.warning("Batch execute failed, falling back to sequential: %s", e)

    # Fallback: sequential execution
    result = {
        "success": True,
        "actors_spawned": 0,
        "actors_failed": 0,
        "images": [],
        "errors": [],
        "command_results": [],
    }

    for i, cmd in enumerate(commands):
        ctype = cmd.get("type", "unknown")
        try:
            r = _exec_one(bridge, cmd, renders_dir)
            result["command_results"].append(
                {"index": i, "type": ctype, "result": r}
            )

            if ctype in ("spawn_actor", "spawn_blueprint", "spawn_static_mesh"):
                if r.get("success"):
                    result["actors_spawned"] += 1
                else:
                    result["actors_failed"] += 1
                    result["errors"].append(
                        f"Cmd {i} ({ctype}): {r.get('error', 'unknown')}"
                    )

            elif ctype == "capture_screenshot" and r.get("success"):
                fname = r.get("filename") or cmd.get("filename", "")
                result["images"].append(f"{renders_dir}/{fname}")

        except Exception as exc:
            result["errors"].append(f"Cmd {i} ({ctype}): {exc}")
            result["command_results"].append(
                {"index": i, "type": ctype, "error": str(exc)}
            )

    if result["actors_failed"] > 0 and result["actors_spawned"] == 0:
        result["success"] = False

    # Restore camera to where the user was looking before the pipeline
    if saved_camera:
        try:
            bridge.set_camera(
                location=saved_camera.get("location", [0, 0, 500]),
                rotation=saved_camera.get("rotation", [-30, 0, 0]),
            )
        except Exception:
            pass

    return result


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    commands_file = sys.argv[1] if len(sys.argv) > 1 else "workspace/scene_commands.json"
    renders_dir = sys.argv[2] if len(sys.argv) > 2 else "renders"
    Path(renders_dir).mkdir(parents=True, exist_ok=True)

    result = execute_scene(commands_file, renders_dir)

    out = Path("workspace/render_result.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))

    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
