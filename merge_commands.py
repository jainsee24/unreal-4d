#!/usr/bin/env python3
"""Merge all worker command files into a single scene_commands.json.

Reads workspace/worker_*_commands.json files (produced by BuilderWorker agents)
and workspace/master_plan.json (produced by MasterArchitect) and combines them
into workspace/scene_commands.json with proper ordering:

  1. clear_scene  (if mode == "new")
  2. set_weather  (from master plan)
  3. All spawn_actor commands from all workers
  4. wait
  5. set_camera + capture_screenshot for each camera angle

Usage:
    python merge_commands.py [mode]

    mode: "new" (default) or "edit"
          "new"  => prepend a clear_scene command
          "edit" => skip clear_scene, keep existing actors
"""

import json
import sys
from pathlib import Path

WORKSPACE = Path(__file__).parent / "workspace"


def load_json(path: Path) -> dict | None:
    """Load a JSON file, returning None if it doesn't exist or is invalid."""
    if not path.exists():
        print(f"  WARN: {path} not found, skipping")
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  WARN: Failed to read {path}: {e}")
        return None


def merge(mode: str = "new") -> dict:
    """Merge all partial command files into one scene_commands.json."""
    commands: list[dict] = []

    # ------------------------------------------------------------------
    # 1. clear_scene (only in "new" mode)
    # ------------------------------------------------------------------
    if mode == "new":
        commands.append({"type": "clear_scene"})

    # ------------------------------------------------------------------
    # 2. set_weather from master_plan.json
    # ------------------------------------------------------------------
    master_plan = load_json(WORKSPACE / "master_plan.json")
    weather_cmd = None
    camera_suggestions = []

    if master_plan:
        weather = master_plan.get("weather", {})
        if weather:
            weather_cmd = {
                "type": "set_weather",
                "preset": weather.get("preset", "clear_noon"),
            }
            params = weather.get("params")
            if params:
                weather_cmd["params"] = params
            commands.append(weather_cmd)

        camera_suggestions = master_plan.get("camera_suggestions", [])

    # ------------------------------------------------------------------
    # 3. Collect spawn_actor commands from all worker files
    # ------------------------------------------------------------------
    zones = ["nw", "ne", "sw", "se"]
    task_ids = [0, 1, 2, 3]
    total_spawn = 0
    files_read = 0

    for zone in zones:
        for tid in task_ids:
            worker_file = WORKSPACE / f"worker_{zone}_{tid}_commands.json"
            data = load_json(worker_file)
            if data is None:
                continue
            files_read += 1
            worker_cmds = data.get("commands", [])
            for cmd in worker_cmds:
                if cmd.get("type") in ("spawn_actor", "spawn_static_mesh", "spawn_blueprint"):
                    commands.append(cmd)
                    total_spawn += 1

    print(f"  Merged {total_spawn} spawn commands from {files_read} worker files")

    # ------------------------------------------------------------------
    # 4. wait — let physics settle
    # ------------------------------------------------------------------
    commands.append({"type": "wait", "seconds": 1.5})

    # ------------------------------------------------------------------
    # 5. Camera setup + screenshots
    # ------------------------------------------------------------------
    if not camera_suggestions:
        # Fallback: one aerial + one street-level
        camera_suggestions = [
            {
                "name": "aerial_overview",
                "location": [0, 0, 15000],
                "rotation": [-90, 0, 0],
                "fov": 90,
            },
            {
                "name": "street_level",
                "location": [-5000, -5000, 300],
                "rotation": [-10, 45, 0],
                "fov": 90,
            },
        ]

    for cam in camera_suggestions:
        loc = cam.get("location", [0, 0, 5000])
        rot = cam.get("rotation", [-45, 0, 0])
        fov = cam.get("fov", 90)
        name = cam.get("name", "view")

        commands.append({
            "type": "set_camera",
            "location": loc,
            "rotation": rot,
            "fov": fov,
        })
        commands.append({
            "type": "capture_screenshot",
            "filename": f"{name}.png",
            "width": 1920,
            "height": 1080,
            "camera_location": loc,
            "camera_rotation": rot,
        })

    return {"commands": commands}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "new"
    if mode not in ("new", "edit"):
        print(f"  ERROR: Unknown mode '{mode}'. Use 'new' or 'edit'.")
        raise SystemExit(1)

    print(f"\n  Merge Commands — mode={mode}")
    print(f"  {'=' * 50}")

    result = merge(mode)

    out_path = WORKSPACE / "scene_commands.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    total = len(result["commands"])
    spawn_count = sum(1 for c in result["commands"] if c["type"] in ("spawn_actor", "spawn_static_mesh"))
    screenshot_count = sum(1 for c in result["commands"] if c["type"] == "capture_screenshot")

    print(f"  Total commands:  {total}")
    print(f"  Spawn commands:  {spawn_count}")
    print(f"  Screenshots:     {screenshot_count}")
    print(f"  Written to:      {out_path}")
    print(f"  {'=' * 50}\n")

    raise SystemExit(0)


if __name__ == "__main__":
    main()
