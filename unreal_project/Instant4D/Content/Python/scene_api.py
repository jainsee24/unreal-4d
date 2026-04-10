"""UE5 Python API for scene building — runs inside the Unreal Editor Python interpreter.

This script can be executed via UE5's Python Remote Execution or the Python console.
It provides high-level functions for scene manipulation that mirror the HTTP API.
"""

import unreal
import json
import os

# ── Paths ──────────────────────────────────────────────────────────────────────

RENDERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "renders")
os.makedirs(RENDERS_DIR, exist_ok=True)

# ── Actor spawning ─────────────────────────────────────────────────────────────

def spawn_actor(asset_path, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), label=""):
    """Spawn an actor from a Blueprint or class path."""
    actor_class = unreal.load_class(None, asset_path)
    if actor_class is None:
        asset = unreal.load_asset(asset_path)
        if asset and hasattr(asset, "generated_class"):
            actor_class = asset.generated_class()

    loc = unreal.Vector(location[0], location[1], location[2])
    rot = unreal.Rotator(rotation[0], rotation[1], rotation[2])

    if actor_class:
        actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
            actor_class, loc, rot
        )
    else:
        actor = unreal.EditorLevelLibrary.spawn_actor_from_object(
            unreal.load_asset(asset_path), loc, rot
        )

    if actor:
        actor.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))
        if label:
            actor.set_actor_label(label)
        unreal.log(f"Spawned: {asset_path} at {location} as '{label}'")
        return actor

    unreal.log_warning(f"Failed to spawn: {asset_path}")
    return None


def spawn_static_mesh(mesh_path, location=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), label=""):
    """Spawn a static mesh actor."""
    mesh = unreal.load_asset(mesh_path)
    if mesh is None:
        unreal.log_warning(f"Mesh not found: {mesh_path}")
        return None

    loc = unreal.Vector(location[0], location[1], location[2])
    rot = unreal.Rotator(rotation[0], rotation[1], rotation[2])

    actor = unreal.EditorLevelLibrary.spawn_actor_from_object(mesh, loc, rot)
    if actor:
        actor.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))
        if label:
            actor.set_actor_label(label)
    return actor


# ── Camera ─────────────────────────────────────────────────────────────────────

def set_camera(location=(0, 0, 500), rotation=(-30, 0, 0)):
    """Move the editor viewport camera."""
    loc = unreal.Vector(location[0], location[1], location[2])
    rot = unreal.Rotator(rotation[0], rotation[1], rotation[2])
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(loc, rot)


# ── Weather ────────────────────────────────────────────────────────────────────

def set_weather(time_of_day=14.0, cloud_density=0.3, fog_density=0.0):
    """Adjust sky atmosphere and fog. Requires Sky Atmosphere and Fog actors in level."""
    actors = unreal.EditorLevelLibrary.get_all_level_actors()
    for a in actors:
        # Directional light (sun)
        if isinstance(a, unreal.DirectionalLight):
            sun_pitch = (time_of_day - 12.0) * 15.0 - 90.0
            a.set_actor_rotation(unreal.Rotator(sun_pitch, 0, 0), False)

        # Exponential height fog
        if isinstance(a, unreal.ExponentialHeightFog):
            fog_comp = a.get_component_by_class(unreal.ExponentialHeightFogComponent)
            if fog_comp:
                fog_comp.set_editor_property("fog_density", fog_density)


# ── Screenshots ────────────────────────────────────────────────────────────────

def capture_screenshot(filename="capture.png", width=1920, height=1080):
    """Capture a screenshot from the current editor viewport."""
    path = os.path.join(RENDERS_DIR, filename)
    unreal.AutomationLibrary.take_high_res_screenshot(
        width, height, path
    )
    unreal.log(f"Screenshot saved: {path}")
    return path


# ── Scene management ───────────────────────────────────────────────────────────

def clear_spawned_actors():
    """Destroy all actors with 'instant4d' tag or spawned label prefix."""
    actors = unreal.EditorLevelLibrary.get_all_level_actors()
    count = 0
    for a in actors:
        label = a.get_actor_label()
        if label and (label.startswith("i4d_") or "instant4d" in label.lower()):
            a.destroy_actor()
            count += 1
    unreal.log(f"Cleared {count} Instant4D actors")
    return count


def get_scene_info():
    """Return current scene summary."""
    actors = unreal.EditorLevelLibrary.get_all_level_actors()
    return {
        "total_actors": len(actors),
        "level": unreal.EditorLevelLibrary.get_editor_world().get_name(),
    }


# ── Batch execute from JSON ────────────────────────────────────────────────────

def execute_commands_file(commands_file):
    """Read a scene_commands.json and execute each command."""
    with open(commands_file) as f:
        data = json.load(f)

    results = []
    for cmd in data.get("commands", []):
        ctype = cmd.get("type", "")
        try:
            if ctype in ("spawn_actor", "spawn_blueprint"):
                actor = spawn_actor(
                    cmd["asset"],
                    cmd.get("location", [0, 0, 0]),
                    cmd.get("rotation", [0, 0, 0]),
                    cmd.get("scale", [1, 1, 1]),
                    label=cmd.get("name", ""),
                )
                results.append({"type": ctype, "success": actor is not None})

            elif ctype == "spawn_static_mesh":
                actor = spawn_static_mesh(
                    cmd["asset"],
                    cmd.get("location", [0, 0, 0]),
                    cmd.get("rotation", [0, 0, 0]),
                    cmd.get("scale", [1, 1, 1]),
                    label=cmd.get("name", ""),
                )
                results.append({"type": ctype, "success": actor is not None})

            elif ctype == "set_camera":
                set_camera(cmd.get("location", [0, 0, 500]), cmd.get("rotation", [-30, 0, 0]))
                results.append({"type": ctype, "success": True})

            elif ctype == "set_weather":
                params = cmd.get("params", {})
                set_weather(
                    time_of_day=params.get("time_of_day", 14.0),
                    cloud_density=params.get("cloud_density", 0.3),
                    fog_density=params.get("fog_density", 0.0),
                )
                results.append({"type": ctype, "success": True})

            elif ctype == "capture_screenshot":
                path = capture_screenshot(
                    cmd.get("filename", "capture.png"),
                    cmd.get("width", 1920),
                    cmd.get("height", 1080),
                )
                results.append({"type": ctype, "success": True, "path": path})

            elif ctype == "clear_scene":
                clear_spawned_actors()
                results.append({"type": ctype, "success": True})

            else:
                results.append({"type": ctype, "success": True, "note": "no-op"})

        except Exception as e:
            results.append({"type": ctype, "success": False, "error": str(e)})

    return results
