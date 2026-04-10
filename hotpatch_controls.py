"""Hot-patch: fix A/D swap, camera tilt, and visible player character."""
import unreal
import uuid
import gc
import math

# Find the handler's globals
handler_globals = None
for obj in gc.get_objects():
    if isinstance(obj, type) and obj.__name__ == "SceneAPIHandler":
        for method_name in ["_handle_post", "do_POST", "do_GET"]:
            method = getattr(obj, method_name, None)
            if method and hasattr(method, "__globals__"):
                handler_globals = method.__globals__
                break
        break

if handler_globals is None:
    for obj in gc.get_objects():
        if isinstance(obj, dict) and "_spawn_actor" in obj and callable(obj.get("_spawn_actor")):
            handler_globals = obj
            break

if handler_globals is None:
    unreal.log_error("[HOTPATCH-controls] Could not find handler globals!")
else:
    spawned_actors_ref = handler_globals.get("_spawned_actors", {})
    _PLAYER_LABEL = handler_globals.get("_PLAYER_LABEL", "i4d_player_character")
    _TP_CAM_BEHIND = handler_globals.get("_TP_CAM_BEHIND", 300.0)
    _TP_CAM_ABOVE = handler_globals.get("_TP_CAM_ABOVE", 200.0)
    _PLAYER_WALK_SPEED = handler_globals.get("_PLAYER_WALK_SPEED", 600.0)

    # --- Fix 1: Camera move with A/D swap fixed + roll=0 ---
    def _patched_move_camera(params):
        loc, rot = unreal.EditorLevelLibrary.get_level_viewport_camera_info()
        command = params.get("command", "")
        speed = float(params.get("speed", 5.0))

        if command == "move_forward":
            yaw_rad = math.radians(rot.yaw)
            loc.x += math.cos(yaw_rad) * speed * 50
            loc.y += math.sin(yaw_rad) * speed * 50
        elif command == "move_backward":
            yaw_rad = math.radians(rot.yaw)
            loc.x -= math.cos(yaw_rad) * speed * 50
            loc.y -= math.sin(yaw_rad) * speed * 50
        elif command == "move_left":
            yaw_rad = math.radians(rot.yaw)
            loc.x += math.sin(yaw_rad) * speed * 50
            loc.y -= math.cos(yaw_rad) * speed * 50
        elif command == "move_right":
            yaw_rad = math.radians(rot.yaw)
            loc.x -= math.sin(yaw_rad) * speed * 50
            loc.y += math.cos(yaw_rad) * speed * 50
        elif command == "move_up":
            loc.z += speed * 50
        elif command == "move_down":
            loc.z -= speed * 50
        elif command == "rotate":
            rot.yaw += float(params.get("dyaw", 0))
            rot.pitch = max(-89, min(89, rot.pitch + float(params.get("dpitch", 0))))
        elif command == "zoom":
            delta = float(params.get("delta", 0))
            yaw_rad = math.radians(rot.yaw)
            loc.x += math.cos(yaw_rad) * delta * 150
            loc.y += math.sin(yaw_rad) * delta * 150
        elif command == "set_position":
            loc.x = float(params.get("x", loc.x))
            loc.y = float(params.get("y", loc.y))
            loc.z = float(params.get("z", loc.z))
            rot.pitch = float(params.get("pitch", rot.pitch))
            rot.yaw = float(params.get("yaw", rot.yaw))

        rot.roll = 0  # ALWAYS force roll=0 to prevent tilted view
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(loc, rot)
        return {"success": True, "camera": {"location": [loc.x, loc.y, loc.z], "rotation": [rot.pitch, rot.yaw, rot.roll]}}

    # --- Fix 2: Third-person camera with roll=0 ---
    def _patched_update_tp_camera(player_loc, yaw):
        yaw_rad = math.radians(yaw)
        cam_x = player_loc[0] - math.cos(yaw_rad) * _TP_CAM_BEHIND
        cam_y = player_loc[1] - math.sin(yaw_rad) * _TP_CAM_BEHIND
        cam_z = player_loc[2] + _TP_CAM_ABOVE
        cam_loc = unreal.Vector(cam_x, cam_y, cam_z)
        target_z = player_loc[2] + 120.0
        dx = player_loc[0] - cam_x
        dy = player_loc[1] - cam_y
        dz = target_z - cam_z
        pitch = math.degrees(math.atan2(dz, math.sqrt(dx * dx + dy * dy)))
        cam_rot = unreal.Rotator(pitch, yaw, 0)  # roll=0
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(cam_loc, cam_rot)

    # --- Fix 3: Visible player character (body + head) ---
    _player_head_ref = [None]  # use list to allow mutation in closure

    def _patched_spawn_player_character(location=None):
        if location is None:
            location = [0.0, 0.0, 100.0]

        handler_globals["_PLAYER_YAW"] = 0.0

        # Destroy previous player parts
        old_char = handler_globals.get("_player_character")
        if old_char is not None:
            try:
                if old_char.is_valid():
                    old_char.destroy_actor()
            except Exception:
                pass
            spawned_actors_ref.pop(_PLAYER_LABEL, None)
        old_head = _player_head_ref[0]
        if old_head is not None:
            try:
                if old_head.is_valid():
                    old_head.destroy_actor()
            except Exception:
                pass
            spawned_actors_ref.pop("_i4d_player_head", None)
        handler_globals["_player_character"] = None
        _player_head_ref[0] = None

        # Body: bright RED cylinder (torso+legs)
        body_loc = unreal.Vector(location[0], location[1], location[2] + 90.0)
        body = unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.StaticMeshActor.static_class(), body_loc, unreal.Rotator(0, 0, 0)
        )
        if not body:
            return {"success": False, "error": "Failed to spawn player body"}

        cylinder_mesh = unreal.load_asset("/Engine/BasicShapes/Cylinder")
        body_comp = body.get_component_by_class(unreal.StaticMeshComponent)
        if body_comp and cylinder_mesh:
            body_comp.set_static_mesh(cylinder_mesh)
            body_comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        body.set_actor_scale3d(unreal.Vector(0.4, 0.4, 0.9))

        red_mat = unreal.load_asset("/Engine/EditorMaterials/WidgetMaterial_X")
        if red_mat and body_comp:
            body_comp.set_material(0, red_mat)

        body.set_actor_label(_PLAYER_LABEL)
        handler_globals["_player_character"] = body
        spawned_actors_ref[_PLAYER_LABEL] = body

        # Head: GREEN sphere on top
        head_z = location[2] + 190.0
        head_loc = unreal.Vector(location[0], location[1], head_z)
        head = unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.StaticMeshActor.static_class(), head_loc, unreal.Rotator(0, 0, 0)
        )
        if head:
            sphere_mesh = unreal.load_asset("/Engine/BasicShapes/Sphere")
            head_comp = head.get_component_by_class(unreal.StaticMeshComponent)
            if head_comp and sphere_mesh:
                head_comp.set_static_mesh(sphere_mesh)
                head_comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            head.set_actor_scale3d(unreal.Vector(0.25, 0.25, 0.3))
            yellow_mat = unreal.load_asset("/Engine/EditorMaterials/WidgetMaterial_Y")
            if yellow_mat and head_comp:
                head_comp.set_material(0, yellow_mat)
            head.set_actor_label("_i4d_player_head")
            _player_head_ref[0] = head
            spawned_actors_ref["_i4d_player_head"] = head

        _patched_update_tp_camera(location, 0.0)
        unreal.log("[Instant4D] Player character spawned — bright red body + green head!")
        return {"success": True, "player": {"location": location, "yaw": 0.0}}

    # --- Fix 4: Player movement with A/D swap fixed + head follows ---
    def _patched_move_player(params):
        _player_character = handler_globals.get("_player_character")
        _actor_valid = handler_globals.get("_actor_valid", lambda a: a is not None)

        if _player_character is None or not _actor_valid(_player_character):
            return {"success": False, "error": "No player character. Call /api/player/spawn first."}

        _PLAYER_YAW = handler_globals.get("_PLAYER_YAW", 0.0)
        command = params.get("command", "")
        speed = float(params.get("speed", 1.0))
        dist = _PLAYER_WALK_SPEED * speed * 0.033

        loc = _player_character.get_actor_location()

        if command == "move_forward":
            yaw_rad = math.radians(_PLAYER_YAW)
            loc.x += math.cos(yaw_rad) * dist
            loc.y += math.sin(yaw_rad) * dist
        elif command == "move_backward":
            yaw_rad = math.radians(_PLAYER_YAW)
            loc.x -= math.cos(yaw_rad) * dist
            loc.y -= math.sin(yaw_rad) * dist
        elif command == "move_left":
            yaw_rad = math.radians(_PLAYER_YAW)
            loc.x += math.sin(yaw_rad) * dist
            loc.y -= math.cos(yaw_rad) * dist
        elif command == "move_right":
            yaw_rad = math.radians(_PLAYER_YAW)
            loc.x -= math.sin(yaw_rad) * dist
            loc.y += math.cos(yaw_rad) * dist
        elif command == "move_up":
            loc.z += dist * 0.7
        elif command == "move_down":
            loc.z -= dist * 0.7
        elif command == "rotate":
            _PLAYER_YAW += float(params.get("dyaw", 0))
        elif command == "set_position":
            loc.x = float(params.get("x", loc.x))
            loc.y = float(params.get("y", loc.y))
            loc.z = float(params.get("z", loc.z))
            if "yaw" in params:
                _PLAYER_YAW = float(params["yaw"])

        handler_globals["_PLAYER_YAW"] = _PLAYER_YAW

        _player_character.set_actor_location(loc, False, False)
        _player_character.set_actor_rotation(unreal.Rotator(0, _PLAYER_YAW, 0), False)

        # Move head to stay on top
        head = _player_head_ref[0]
        if head is not None and _actor_valid(head):
            head_loc = unreal.Vector(loc.x, loc.y, loc.z + 100.0)
            head.set_actor_location(head_loc, False, False)

        player_loc = [loc.x, loc.y, loc.z - 90.0]
        _patched_update_tp_camera(player_loc, _PLAYER_YAW)

        return {"success": True, "player": {"location": player_loc, "yaw": _PLAYER_YAW}}

    # Apply all patches
    handler_globals["_move_camera"] = _patched_move_camera
    handler_globals["_update_tp_camera"] = _patched_update_tp_camera
    handler_globals["_spawn_player_character"] = _patched_spawn_player_character
    handler_globals["_move_player"] = _patched_move_player
    handler_globals["_player_head"] = None

    # Also fix the camera RIGHT NOW — reset roll to 0
    loc, rot = unreal.EditorLevelLibrary.get_level_viewport_camera_info()
    rot.roll = 0
    unreal.EditorLevelLibrary.set_level_viewport_camera_info(loc, rot)

    unreal.log("[HOTPATCH-controls] ALL FIXES APPLIED:")
    unreal.log("  1. Camera roll forced to 0 (no more tilt)")
    unreal.log("  2. A/D keys swapped (left=left, right=right)")
    unreal.log("  3. Visible player: RED body + GREEN head")
    unreal.log("  4. Camera now reset to straight")
