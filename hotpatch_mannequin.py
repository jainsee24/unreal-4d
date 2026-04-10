"""Hot-patch: Replace cylinder+sphere player with real UE5 Mannequin character."""
import unreal
import math
import gc

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
    unreal.log_error("[MANNEQUIN] Could not find handler globals!")
else:
    spawned_actors_ref = handler_globals.get("_spawned_actors", {})
    _PLAYER_LABEL = handler_globals.get("_PLAYER_LABEL", "i4d_player_character")
    _TP_CAM_BEHIND = 400.0   # Slightly further back for mannequin
    _TP_CAM_ABOVE = 200.0
    _PLAYER_WALK_SPEED = handler_globals.get("_PLAYER_WALK_SPEED", 600.0)

    def _patched_update_tp_camera(player_loc, yaw):
        yaw_rad = math.radians(yaw)
        cam_x = player_loc[0] - math.cos(yaw_rad) * _TP_CAM_BEHIND
        cam_y = player_loc[1] - math.sin(yaw_rad) * _TP_CAM_BEHIND
        cam_z = player_loc[2] + _TP_CAM_ABOVE
        cam_loc = unreal.Vector(cam_x, cam_y, cam_z)
        target_z = player_loc[2] + 100.0  # Look at chest height
        dx = player_loc[0] - cam_x
        dy = player_loc[1] - cam_y
        dz = target_z - cam_z
        pitch = math.degrees(math.atan2(dz, math.sqrt(dx * dx + dy * dy)))
        cam_rot = unreal.Rotator(pitch, yaw, 0)
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(cam_loc, cam_rot)

    def _patched_spawn_player(location=None):
        """Spawn the UE5 Mannequin as the player character."""
        if location is None:
            location = [0.0, 0.0, 100.0]

        handler_globals["_PLAYER_YAW"] = 0.0

        # Destroy previous player
        old_char = handler_globals.get("_player_character")
        _actor_valid = handler_globals.get("_actor_valid", lambda a: a is not None)
        if old_char is not None and _actor_valid(old_char):
            old_char.destroy_actor()
            spawned_actors_ref.pop(_PLAYER_LABEL, None)

        # Destroy old head too (from cylinder+sphere version)
        old_head_ref = handler_globals.get("_player_head")
        if old_head_ref is not None and _actor_valid(old_head_ref):
            old_head_ref.destroy_actor()
            spawned_actors_ref.pop("_i4d_player_head", None)
        handler_globals["_player_head"] = None
        handler_globals["_player_character"] = None

        loc = unreal.Vector(location[0], location[1], location[2])
        rot = unreal.Rotator(0, 0, 0)

        # Try to load the mannequin SkeletalMesh
        mannequin_mesh = unreal.load_asset("/Game/Mannequin/Character/Mesh/SK_Mannequin")
        mannequin_female = unreal.load_asset("/Game/Mannequin/Character/Mesh/SK_Mannequin_Female")

        mesh_to_use = mannequin_mesh or mannequin_female

        if mesh_to_use and isinstance(mesh_to_use, unreal.SkeletalMesh):
            # Spawn a SkeletalMeshActor
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.SkeletalMeshActor.static_class(), loc, rot
            )
            if actor:
                skel_comp = actor.get_component_by_class(unreal.SkeletalMeshComponent)
                if skel_comp:
                    skel_comp.set_skeletal_mesh_asset(mesh_to_use)
                    skel_comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
                    # Native materials (M_Male_Body + M_UE4Man_ChestLogo) are applied
                    # automatically by the mesh — no override needed.

                # Scale to normal human size (mannequin default is human-sized)
                actor.set_actor_scale3d(unreal.Vector(1.0, 1.0, 1.0))
                actor.set_actor_label(_PLAYER_LABEL)
                handler_globals["_player_character"] = actor
                spawned_actors_ref[_PLAYER_LABEL] = actor

                _patched_update_tp_camera(location, 0.0)
                unreal.log("[MANNEQUIN] Real UE5 Mannequin player spawned!")
                return {"success": True, "player": {"location": location, "yaw": 0.0, "character": "mannequin"}}
            else:
                unreal.log_warning("[MANNEQUIN] Failed to spawn SkeletalMeshActor, falling back to StaticMesh")

        # Fallback: use a visible cylinder if mannequin fails
        unreal.log_warning("[MANNEQUIN] Mannequin not available, using fallback character")
        body_loc = unreal.Vector(location[0], location[1], location[2] + 90.0)
        body = unreal.EditorLevelLibrary.spawn_actor_from_class(
            unreal.StaticMeshActor.static_class(), body_loc, unreal.Rotator(0, 0, 0)
        )
        if body:
            cyl = unreal.load_asset("/Engine/BasicShapes/Cylinder")
            bc = body.get_component_by_class(unreal.StaticMeshComponent)
            if bc and cyl:
                bc.set_static_mesh(cyl)
                bc.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
            body.set_actor_scale3d(unreal.Vector(0.4, 0.4, 0.9))
            red_mat = unreal.load_asset("/Engine/EditorMaterials/WidgetMaterial_X")
            if red_mat and bc:
                bc.set_material(0, red_mat)
            body.set_actor_label(_PLAYER_LABEL)
            handler_globals["_player_character"] = body
            spawned_actors_ref[_PLAYER_LABEL] = body

        _patched_update_tp_camera(location, 0.0)
        return {"success": True, "player": {"location": location, "yaw": 0.0, "character": "fallback"}}

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

        # No separate head to move for mannequin
        player_loc = [loc.x, loc.y, loc.z]
        _patched_update_tp_camera(player_loc, _PLAYER_YAW)

        return {"success": True, "player": {"location": player_loc, "yaw": _PLAYER_YAW}}

    # Apply patches
    handler_globals["_spawn_player_character"] = _patched_spawn_player
    handler_globals["_move_player"] = _patched_move_player
    handler_globals["_update_tp_camera"] = _patched_update_tp_camera

    unreal.log("[MANNEQUIN] Hotpatch applied! Player will now use UE5 Mannequin mesh.")
