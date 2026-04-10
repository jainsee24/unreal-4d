"""Hot-patch _spawn_actor to properly handle StaticMesh assets from Polyhaven."""
import unreal
import uuid

def _spawn_actor_v2(asset, location, rotation=None, scale=None, name=None, properties=None):
    loc = unreal.Vector(location[0], location[1], location[2])
    rot = unreal.Rotator(rotation[0], rotation[1], rotation[2]) if rotation else unreal.Rotator(0, 0, 0)
    actor_name = name or ("i4d_" + uuid.uuid4().hex[:8])

    actor = None

    # 1. Try loading as blueprint class first
    actor_class = unreal.load_class(None, asset)
    if actor_class is None:
        bp = unreal.load_asset(asset)
        if bp and hasattr(bp, "generated_class"):
            actor_class = bp.generated_class()

    if actor_class:
        actor = unreal.EditorLevelLibrary.spawn_actor_from_class(actor_class, loc, rot)
    else:
        # 2. Try as static mesh — spawn StaticMeshActor and set the mesh
        asset_obj = unreal.load_asset(asset)
        if asset_obj is None:
            # Try without the .AssetName suffix (just package path)
            package_path = asset.rsplit(".", 1)[0] if "." in asset else asset
            asset_obj = unreal.load_asset(package_path)

        if asset_obj and isinstance(asset_obj, unreal.StaticMesh):
            # Spawn a StaticMeshActor and assign the mesh
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.StaticMeshActor.static_class(), loc, rot
            )
            if actor:
                mesh_comp = actor.get_component_by_class(unreal.StaticMeshComponent)
                if mesh_comp:
                    mesh_comp.set_static_mesh(asset_obj)
                    mesh_comp.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
                unreal.log("[Instant4D] Spawned StaticMesh: " + asset + " as " + actor_name)
        elif asset_obj:
            # 3. Try generic spawn_actor_from_object
            actor = unreal.EditorLevelLibrary.spawn_actor_from_object(asset_obj, loc, rot)

    if actor:
        if scale:
            actor.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))
        actor.set_actor_label(actor_name)
        _spawned_actors[actor_name] = actor
        unreal.log("[Instant4D] Spawned: " + asset + " as '" + actor_name + "'")
        return {"success": True, "actor_id": actor_name, "asset": asset}
    else:
        unreal.log_warning("[Instant4D] Failed to spawn: " + asset + " — spawning placeholder cube")
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
            _spawned_actors[actor_name] = placeholder
            return {"success": True, "actor_id": actor_name, "asset": asset, "note": "placeholder"}
        return {"success": False, "error": "Could not load asset: " + asset}

# Replace the global function
_spawn_actor = _spawn_actor_v2
globals()["_spawn_actor"] = _spawn_actor_v2
unreal.log("[HOTPATCH] _spawn_actor replaced with v2 (StaticMesh support)")
