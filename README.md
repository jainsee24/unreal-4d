# Instant4D

Real-time 3D scene generator powered by Unreal Engine 5 and Claude AI agents. Control a textured character in a live UE5 viewport from your browser with WASD controls, MJPEG streaming, and multi-agent scene generation.

## Prerequisites

- **Unreal Engine 5.5** (built from source on Linux)
- **Python 3.10+**
- **PythonScriptPlugin** enabled in your UE5 project

### UE5 Project Setup

The system expects a UE5 project at a configurable path with these plugins enabled (in `.uproject`):

```json
{
  "Plugins": [
    {"Name": "PythonScriptPlugin", "Enabled": true},
    {"Name": "MoverExamples", "Enabled": true}
  ]
}
```

Copy `unreal_project/Instant4D/Content/Python/scene_api.py` logic into your project's `Content/Python/init_unreal.py` to enable the HTTP API server inside UE5.

## Quick Start

```bash
# 1. Clone
git clone https://github.com/jainsee24/unreal-4d.git
cd unreal-4d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run everything (downloads assets, starts UE5, imports character, launches web UI)
./run.sh
```

The script will:
1. Download Mixamo character FBX files (mesh + walk/idle/run/jump animations)
2. Launch UE5 in headless mode (`-RenderOffscreen`)
3. Import the character assets into UE5 via Python API
4. Start the Flask web UI

Once ready, open **http://localhost:5555** in your browser.

## Configuration

Edit the paths at the top of `run.sh` to match your UE5 installation:

```bash
UE_EDITOR="/path/to/UnrealEditor"
UE_PROJECT="/path/to/YourProject.uproject"
```

### CLI Options

```
./run.sh [--port PORT] [--ue-port PORT] [--skip-assets]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | 5555 | Web UI port |
| `--ue-port` | 8000 | UE5 API server port |
| `--skip-assets` | false | Skip character FBX download and import |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Required for AI agent scene generation |
| `UE_API_URL` | Override UE5 API URL (default: `http://localhost:8000`) |

## Controls

| Key | Action |
|-----|--------|
| WASD | Move camera / walk character |
| Mouse | Look around (click viewport first) |
| Q / E | Fly up / down (free camera) |
| Scroll | Zoom |
| Shift | Sprint |
| ESC | Release mouse |
| Play button | Toggle third-person character mode |

## Architecture

```
Browser (localhost:5555)
    │
    ├── MJPEG stream ← UE5 viewport capture @ 30fps
    │
    └── REST API ──→ Flask proxy (web.py)
                         │
                         └──→ UE5 Python API (port 8000)
                                  │
                                  ├── init_unreal.py (HTTP server inside UE5)
                                  ├── Game-thread command queue
                                  ├── Player character (Mixamo Guard)
                                  └── Scene manipulation (spawn, camera, weather)
```

### Key Files

| File | Description |
|------|-------------|
| `run.sh` | Single entry point — downloads assets, starts UE5, launches UI |
| `web.py` | Flask web server with proxy to UE5 API |
| `static/js/app.js` | Frontend with WASD controls and MJPEG viewer |
| `templates/index.html` | Web UI layout |
| `ue_bridge.py` | Python bridge for UE5 scene commands |
| `pipeline.py` | Multi-agent scene generation pipeline |
| `agents/` | Claude AI agent definitions for scene generation |
| `config.py` | Project configuration |

## API Endpoints

The UE5 API server (default port 8000) exposes:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Server health check |
| `/api/player/spawn` | POST | Spawn player character |
| `/api/player/move` | POST | Move player (batch/individual commands) |
| `/api/player/info` | GET | Get player position and rotation |
| `/api/scene/info` | GET | Scene state (FPS, actors, camera, weather) |
| `/api/scene/clear` | POST | Clear all spawned actors |
| `/api/scene/execute` | POST | Batch execute scene commands |
| `/api/stream` | GET | MJPEG video stream |

## License

MIT
