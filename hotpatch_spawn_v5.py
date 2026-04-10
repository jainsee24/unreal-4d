"""Hot-patch v5 — handle StaticMesh, Material, and other asset types correctly.
Key fix: load_asset() for imported FBX assets might return Material instead of StaticMesh.
We need to explicitly load the StaticMesh by trying different suffixes.
"""
import unreal
import uuid
import gc
import os

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
    unreal.log_error("[HOTPATCH-v5] Could not find handler globals!")
else:
    spawned_actors_ref = handler_globals.get("_spawned_actors", {})

    def _find_static_mesh(asset_path):
        """Try multiple strategies to find the StaticMesh for a given asset path."""
        # Strategy 1: Direct load - might be StaticMesh already
        obj = unreal.load_asset(asset_path)
        if obj and isinstance(obj, unreal.StaticMesh):
            return obj

        # Strategy 2: Try package path (without .AssetName)
        package_path = asset_path.rsplit(".", 1)[0] if "." in asset_path else asset_path
        obj = unreal.load_asset(package_path)
        if obj and isinstance(obj, unreal.StaticMesh):
            return obj

        # Strategy 3: Use AssetRegistry to find StaticMesh in the same package folder
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        folder = "/".join(package_path.rsplit("/", 1)[:-1]) if "/" in package_path else package_path
        filt = unreal.ARFilter(package_paths=[folder], recursive_paths=False)
        all_assets = ar.get_assets(filt)

        # Find StaticMesh in the same folder whose name matches
        base_name = package_path.rsplit("/", 1)[-1] if "/" in package_path else package_path
        for a in all_assets:
            cls = str(a.asset_class_path.asset_name) if hasattr(a, "asset_class_path") else str(a.asset_class)
            if "StaticMesh" in cls:
                aname = str(a.asset_name)
                pkg = str(a.package_name)
                # Check if names match or are similar
                if base_name in aname or aname in base_name:
                    full_path = pkg + "." + aname
                    mesh = unreal.load_asset(full_path)
                    if mesh and isinstance(mesh, unreal.StaticMesh):
                        return mesh

        # Strategy 4: Try common FBX naming patterns
        # e.g., /Game/Polyhaven/Props/fire_hydrant -> try fire_hydrant_1k
        for suffix in ["_1k", "_2k", "_4k", ""]:
            test_path = package_path + suffix + "." + package_path.rsplit("/", 1)[-1] + suffix
            mesh = unreal.load_asset(test_path)
            if mesh and isinstance(mesh, unreal.StaticMesh):
                return mesh

        return None

    def _patched_spawn_actor(asset, location, rotation=None, scale=None, name=None, properties=None):
        loc = unreal.Vector(location[0], location[1], location[2])
        rot = unreal.Rotator(rotation[0], rotation[1], rotation[2]) if rotation else unreal.Rotator(0, 0, 0)
        actor_name = name or ("i4d_" + uuid.uuid4().hex[:8])
        actor = None

        # 1. Try loading as blueprint class
        actor_class = unreal.load_class(None, asset)
        if actor_class is None:
            bp = unreal.load_asset(asset)
            if bp and hasattr(bp, "generated_class"):
                actor_class = bp.generated_class()

        if actor_class:
            actor = unreal.EditorLevelLibrary.spawn_actor_from_class(actor_class, loc, rot)
        else:
            # 2. Try to find the StaticMesh
            mesh = _find_static_mesh(asset)

            if mesh is not None:
                # Spawn StaticMeshActor and set the real mesh
                actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                    unreal.StaticMeshActor.static_class(), loc, rot
                )
                if actor:
                    mesh_comp = actor.get_component_by_class(unreal.StaticMeshComponent)
                    if mesh_comp:
                        mesh_comp.set_static_mesh(mesh)
                        mesh_comp.set_collision_enabled(unreal.CollisionEnabled.QUERY_AND_PHYSICS)
                    unreal.log("[Instant4D-v5] REAL mesh: " + asset + " -> " + str(mesh.get_name()))
            else:
                # 3. Try generic load + spawn
                asset_obj = unreal.load_asset(asset)
                if asset_obj:
                    actor = unreal.EditorLevelLibrary.spawn_actor_from_object(asset_obj, loc, rot)

        if actor:
            if scale:
                actor.set_actor_scale3d(unreal.Vector(scale[0], scale[1], scale[2]))
            actor.set_actor_label(actor_name)
            spawned_actors_ref[actor_name] = actor
            unreal.log("[Instant4D-v5] Spawned: " + asset + " as " + actor_name)
            return {"success": True, "actor_id": actor_name, "asset": asset}
        else:
            unreal.log_warning("[Instant4D-v5] Failed: " + asset + " — placeholder cube")
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

    # Patch it
    handler_globals["_spawn_actor"] = _patched_spawn_actor

    # Also patch in any other scope
    for obj in gc.get_objects():
        if isinstance(obj, dict) and obj is not handler_globals and "_spawn_actor" in obj and callable(obj.get("_spawn_actor")):
            obj["_spawn_actor"] = _patched_spawn_actor

    unreal.log("[HOTPATCH-v5] SUCCESS! _spawn_actor patched with smart StaticMesh finder")

    # Quick test: verify fire_hydrant mesh can be found
    test_mesh = _find_static_mesh("/Game/Polyhaven/Props/fire_hydrant_1k.fire_hydrant_1k")
    if test_mesh:
        unreal.log("[HOTPATCH-v5] TEST OK: fire_hydrant_1k -> " + str(test_mesh.get_name()) + " (" + str(type(test_mesh).__name__) + ")")
    else:
        unreal.log_warning("[HOTPATCH-v5] TEST FAIL: Could not find fire_hydrant mesh!")
