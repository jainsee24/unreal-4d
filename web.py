"""Flask web UI for Instant4D — Unreal Engine 5 3D Scene Generator."""

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from queue import Queue, Empty

import requests as http_requests
from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from flask_cors import CORS

from config import PipelineConfig, PROJECT_ROOT, get_claude_auth_env, RENDERS_DIR, WORKSPACE_DIR
from pipeline import run_pipeline, PipelineEvent
from parallel_pipeline import run_parallel_pipeline, PipelineEvent as ParallelPipelineEvent
from direct_pipeline import run_direct_pipeline
from ue_bridge import UnrealBridge

log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "instant4d-ue5-dev")

jobs: dict = {}  # job_id → {status, events_queue, result, …}

_bridge: UnrealBridge | None = None


def _get_bridge() -> UnrealBridge:
    global _bridge
    if _bridge is None:
        cfg = PipelineConfig()
        _bridge = UnrealBridge(base_url=cfg.ue_api_url)
    return _bridge


# ═══════════════════════════════════════════════════════════════════════════════
# Pages
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


# ═══════════════════════════════════════════════════════════════════════════════
# Scene generation pipeline
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/generate", methods=["POST"])
def generate_scene():
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    model = data.get("model", "sonnet")
    mode = data.get("mode", "new")
    pipeline = data.get("pipeline", "direct")  # "direct" (fast), "parallel" (22 agents), "sequential" (legacy)
    job_id = str(uuid.uuid4())[:8]

    scene_context = _get_scene_context()

    jobs[job_id] = {
        "id": job_id,
        "prompt": prompt,
        "model": model,
        "mode": mode,
        "pipeline": pipeline,
        "status": "running",
        "events": Queue(),
        "result": None,
        "created_at": time.time(),
    }

    t = threading.Thread(
        target=_run_pipeline_bg,
        args=(job_id, prompt, model, mode, scene_context),
        daemon=True,
    )
    t.start()
    return jsonify({"job_id": job_id, "status": "running", "pipeline": pipeline})


@app.route("/api/events/<job_id>")
def stream_events(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404

    def gen():
        while True:
            try:
                event = job["events"].get(timeout=30)
                if event is None:
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                yield f"data: {json.dumps(event)}\n\n"
            except Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/job/<job_id>")
def get_job(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify({
        "id": job["id"],
        "prompt": job["prompt"],
        "status": job["status"],
        "result": job["result"],
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Images
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/images")
def list_images():
    images = sorted(f.name for f in RENDERS_DIR.glob("*.png")) if RENDERS_DIR.exists() else []
    return jsonify({"images": images})


@app.route("/renders/<path:filename>")
def serve_render(filename):
    return send_from_directory(str(RENDERS_DIR), filename)


# ═══════════════════════════════════════════════════════════════════════════════
# UE5 proxy — all browser→UE5 traffic goes through Flask to avoid CORS/port issues
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/ue5/api/<path:subpath>", methods=["GET", "POST", "DELETE", "OPTIONS"])
def proxy_ue5(subpath):
    """Proxy all /ue5/api/* requests to the real UE5 scene server."""
    bridge = _get_bridge()
    ue_url = f"{bridge.base_url}/api/{subpath}"

    if request.method == "OPTIONS":
        return "", 204

    try:
        if request.method == "GET":
            r = http_requests.get(ue_url, timeout=30, stream=True)
        elif request.method == "POST":
            r = http_requests.post(ue_url, json=request.get_json(silent=True), timeout=30)
        elif request.method == "DELETE":
            r = http_requests.delete(ue_url, timeout=30)
        else:
            return jsonify({"error": "Method not allowed"}), 405

        # For MJPEG stream, proxy the stream directly
        content_type = r.headers.get("Content-Type", "")
        if "multipart" in content_type:
            def stream():
                try:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            yield chunk
                except Exception:
                    pass
            return Response(stream(), content_type=content_type, headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
            })

        # For images (snapshot)
        if "image/" in content_type:
            return Response(r.content, content_type=content_type)

        # For JSON
        return Response(r.content, status=r.status_code,
                        content_type=r.headers.get("Content-Type", "application/json"))
    except Exception as e:
        log.error("UE5 proxy error: %s %s → %s", request.method, subpath, e)
        return jsonify({"success": False, "error": str(e)}), 502


# ═══════════════════════════════════════════════════════════════════════════════
# Scene control  (proxied to UE5 scene server via bridge)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/control", methods=["POST"])
def control_scene():
    data = request.json or {}
    action = data.get("action", "")
    bridge = _get_bridge()

    if action == "load_map":
        level = data.get("map", "Default")
        return jsonify(bridge.load_level(f"/Game/Maps/{level}"))

    if action == "weather":
        preset = data.get("preset", "clear_noon")
        return jsonify(bridge.set_weather(preset=preset))

    if action == "camera":
        loc = [float(data.get("x", 0)), float(data.get("y", 0)), float(data.get("z", 500))]
        rot = [float(data.get("pitch", -30)), float(data.get("yaw", 0)), 0]
        return jsonify(bridge.set_camera(loc, rot))

    if action == "snapshot":
        fname = f"snapshot_{int(time.time())}.png"
        return jsonify(bridge.capture_screenshot(fname))

    if action == "spawn_vehicle":
        asset = data.get("blueprint", "/Game/Vehicles/Sedan/BP_Sedan")
        loc = [float(data.get("x", 0)), float(data.get("y", 0)), float(data.get("z", 0))]
        rot = [0, float(data.get("yaw", 0)), 0]
        return jsonify(bridge.spawn_actor(asset, loc, rot))

    if action == "clear_scene":
        return jsonify(bridge.clear_scene())

    if action == "get_scene_info":
        return jsonify(bridge.get_scene_info())

    return jsonify({"error": f"Unknown action: {action}"}), 400


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _get_scene_context() -> dict:
    """Fetch current UE5 scene state for the pipeline."""
    try:
        info = _get_bridge().get_scene_info()
        if info.get("success"):
            return info
    except Exception as e:
        log.warning("Could not get scene context: %s", e)
    return {
        "level": "unknown",
        "camera": {"location": [0, 0, 500], "rotation": [-30, 0, 0]},
        "vehicles": 0,
        "walkers": 0,
        "props": 0,
    }


def _run_pipeline_bg(job_id, prompt, model, mode, scene_context):
    job = jobs[job_id]
    pipeline_type = job.get("pipeline", "direct")

    def event_cb(event):
        job["events"].put(event.to_dict())

    try:
        config = PipelineConfig()

        if pipeline_type == "direct":
            # Direct-spawn pipeline (5 agents, real-time spawning via curl)
            result = asyncio.run(
                run_direct_pipeline(
                    user_prompt=prompt,
                    config=config,
                    event_callback=event_cb,
                    model=model,
                    mode=mode,
                    scene_context=scene_context,
                )
            )
        elif pipeline_type == "parallel":
            # Hierarchical parallel pipeline (22 agents, file-based)
            result = asyncio.run(
                run_parallel_pipeline(
                    user_prompt=prompt,
                    config=config,
                    event_callback=event_cb,
                    model=model,
                    mode=mode,
                    scene_context=scene_context,
                )
            )
        else:
            # Legacy sequential pipeline
            result = asyncio.run(
                run_pipeline(
                    user_prompt=prompt,
                    config=config,
                    event_callback=event_cb,
                    model=model,
                    mode=mode,
                    scene_context=scene_context,
                )
            )

        job["result"] = result
        job["status"] = "completed" if result.get("success") else "failed"
    except Exception as e:
        log.exception("Pipeline bg failed")
        job["result"] = {"success": False, "error": str(e)}
        job["status"] = "failed"
        job["events"].put({"type": "error", "data": {"message": str(e)}})
    finally:
        job["events"].put(None)  # sentinel


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 5555))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
