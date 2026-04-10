"""Hot-patch v3 — find Instant4DHandler class and patch its globals."""
import unreal
import uuid
import gc

# The new spawn function
def _new_spawn_actor(asset, location, rotation=None, scale=None, name=None, properties=None):
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
        # 2. Load asset and check if it's a StaticMesh
        asset_obj = unreal.load_asset(asset)
        if asset_obj is None:
            package_path = asset.rsplit(".", 1)[0] if "." in asset else asset
            asset_obj = unreal.load_asset(package_path)

        if asset_obj is not None and isinstance(asset_obj, unreal.StaticMesh):
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
        _spawned_actors = _find_spawned_actors()
        if _spawned_actors is not None:
            _spawned_actors[actor_name] = actor
        unreal.log("[Instant4D-v2] Spawned REAL: " + asset + " as " + actor_name)
        return {"success": True, "actor_id": actor_name, "asset": asset}
    else:
        unreal.log_warning("[Instant4D-v2] Failed: " + asset + " — placeholder")
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
            _spawned_actors = _find_spawned_actors()
            if _spawned_actors is not None:
                _spawned_actors[actor_name] = placeholder
            return {"success": True, "actor_id": actor_name, "asset": asset, "note": "placeholder"}
        return {"success": False, "error": "Could not load: " + asset}


def _find_spawned_actors():
    """Find the _spawned_actors dict from the handler's globals."""
    for obj in gc.get_objects():
        if isinstance(obj, dict) and "_spawned_actors" in str(obj.keys())[:200]:
            # Check if this dict has _spawned_actors as a key AND also has _spawn_actor
            pass
    # Fallback: search handler class globals
    for obj in gc.get_objects():
        if isinstance(obj, type) and obj.__name__ == "Instant4DHandler":
            for method_name in ["_handle_post", "do_POST", "do_GET"]:
                method = getattr(obj, method_name, None)
                if method and hasattr(method, "__globals__"):
                    g = method.__globals__
                    if "_spawned_actors" in g:
                        return g["_spawned_actors"]
    return {}


# Find the Instant4DHandler class and patch its globals
patched = False
handler_found = False
for obj in gc.get_objects():
    if isinstance(obj, type) and obj.__name__ == "Instant4DHandler":
        handler_found = True
        unreal.log("[HOTPATCH-v3] Found Instant4DHandler class: " + str(obj))
        # Get any method's __globals__ — they all share the same globals dict
        for method_name in ["_handle_post", "do_POST", "do_GET"]:
            method = getattr(obj, method_name, None)
            if method and hasattr(method, "__globals__"):
                g = method.__globals__
                if "_spawn_actor" in g:
                    old_fn = g["_spawn_actor"]
                    # Make sure new function can access _spawned_actors from the same globals
                    _new_spawn_actor.__globals__["_spawned_actors"] = g.get("_spawned_actors", {})
                    g["_spawn_actor"] = _new_spawn_actor
                    patched = True
                    unreal.log("[HOTPATCH-v3] SUCCESS: Replaced _spawn_actor in handler globals!")
                    unreal.log("[HOTPATCH-v3] _spawned_actors has " + str(len(g.get("_spawned_actors", {}))) + " entries")
                    break
                else:
                    keys = [k for k in g.keys() if "spawn" in k.lower()]
                    unreal.log("[HOTPATCH-v3] spawn-related keys in globals: " + str(keys))
        break

if not handler_found:
    unreal.log_warning("[HOTPATCH-v3] Instant4DHandler class NOT found! Searching all classes...")
    for obj in gc.get_objects():
        if isinstance(obj, type) and hasattr(obj, "_handle_post"):
            unreal.log("[HOTPATCH-v3] Found class with _handle_post: " + obj.__name__)

if not patched:
    unreal.log_warning("[HOTPATCH-v3] FAILED to patch — trying alternative approaches")
    # Try: iterate all dicts that contain _spawn_actor
    count = 0
    for obj in gc.get_objects():
        if isinstance(obj, dict) and "_spawn_actor" in obj and callable(obj.get("_spawn_actor")):
            count += 1
            obj["_spawn_actor"] = _new_spawn_actor
            unreal.log("[HOTPATCH-v3] Patched dict #" + str(count) + " containing _spawn_actor")
            patched = True

unreal.log("[HOTPATCH-v3] Final status: patched=" + str(patched) + " handler_found=" + str(handler_found))
