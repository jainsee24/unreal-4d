"""Hot-patch v4 — target SceneAPIHandler and properly link _spawned_actors."""
import unreal
import uuid
import gc

# Find the handler's globals first to get _spawned_actors reference
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
    # Fallback: find dict with _spawn_actor
    for obj in gc.get_objects():
        if isinstance(obj, dict) and "_spawn_actor" in obj and callable(obj.get("_spawn_actor")):
            handler_globals = obj
            break

if handler_globals is None:
    unreal.log_error("[HOTPATCH-v4] Could not find handler globals!")
else:
    # Get the _spawned_actors dict from handler globals
    spawned_actors_ref = handler_globals.get("_spawned_actors", {})

    def _patched_spawn_actor(asset, location, rotation=None, scale=None, name=None, properties=None):
        loc = unreal.Vector(location[0], location[1], location[2])
        rot = unreal.Rotator(rotation[0], rotation[1], rotation[2]) if rotation else unreal.Rotator(0, 0, 0)
        actor_name = name or ("i4d_" + uuid.uuid4().hex[:8])
        actor = None

        # 1. Try as blueprint class
        actor_class = unreal.load_class(None, asset)
        if actor_class is None:
            bp = unreal.load_asset(asset)
            if bp and hasattr(bp, "generated_class"):
                actor_class = bp.generated_class()

        if actor_class:
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(actor_class, loc, rot)
        else:
            # 2. Load asset
            asset_obj = unreal.load_asset(asset)
            if asset_obj is None:
                package_path = asset.rsplit(".", 1)[0] if "." in asset else asset
                asset_obj = unreal.load_asset(package_path)

            if asset_obj is not None and isinstance(asset_obj, unreal.StaticMesh):
                # REAL StaticMesh — spawn actor and set mesh
                actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                    unreal.StaticMeshActor.static_class(), loc, rot
                )
                if actor:
                    mesh_comp = actor.get_component_by_class(unreal.StaticMeshComponent)
                    if mesh_comp:
                        mesh_comp.set_static_mesh(asset_obj)
                        mesh_comp.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
            elif asset_obj is not None:
                actor = unreal.EditorLevelLibrary.spawn_actor_from_object(asset_obj, loc, rot)

        if actor:
            if scale:
                actor.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))
            actor.set_actor_label(actor_name)
            spawned_actors_ref[actor_name] = actor
            unreal.log("[Instant4D-v4] REAL spawn: " + asset + " as " + actor_name)
            return {"success": True, "actor_id": actor_name, "asset": asset}
        else:
            unreal.log_warning("[Instant4D-v4] Failed: " + asset + " — placeholder")
            placeholder = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.StaticMeshActor.static_class(), loc, rot
            )
            if placeholder:
                cube_mesh = unreal.load_asset("/Engine/BasicShapes/Cube")
                if cube_mesh:
                    mc = placeholder.get_component_by_class(unreal.StaticMeshComponent)
                    if mc:
                        mc.set_static_mesh(cube_mesh)
                placeholder.set_actor_label(actor_name)
                spawned_actors_ref[actor_name] = placeholder
                return {"success": True, "actor_id": actor_name, "asset": asset, "note": "placeholder"}
            return {"success": False, "error": "Could not load: " + asset}

    # Replace in handler globals
    handler_globals["_spawn_actor"] = _patched_spawn_actor

    # Also replace in any other dict that has _spawn_actor
    for obj in gc.get_objects():
        if isinstance(obj, dict) and obj is not handler_globals and "_spawn_actor" in obj and callable(obj.get("_spawn_actor")):
            obj["_spawn_actor"] = _patched_spawn_actor

    unreal.log("[HOTPATCH-v4] SUCCESS: _spawn_actor patched with StaticMesh support!")
    unreal.log("[HOTPATCH-v4] _spawned_actors currently has " + str(len(spawned_actors_ref)) + " entries")
