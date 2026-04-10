#!/usr/bin/env bash
set -euo pipefail

# ─── Instant4D — Unreal Engine 5 Scene Generator ────────────────────────────
# Single entry point: downloads character assets, starts UE5, imports them, launches UI.
# Usage: ./run.sh [--port PORT] [--ue-port PORT] [--skip-assets]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${PORT:-5555}"
UE_PORT="${UE_PORT:-8000}"
SKIP_ASSETS=false

UE_EDITOR="/home/sejain/unreal-engine/Engine/Binaries/Linux/UnrealEditor"
UE_PROJECT="/home/sejain/unreal-engine/SceneCraftProject/SceneCraftProject.uproject"
UE_CONTENT="/home/sejain/unreal-engine/SceneCraftProject/Content"

# FBX download URLs (Mixamo guard character + animations)
FBX_BASE_URL="https://github.com/aaronsnoswell/3DCharacter/raw/master/Mixamo"
declare -A FBX_FILES=(
    ["standard_walk.fbx"]="$FBX_BASE_URL/Standard%20Walk.fbx"
    ["idle.fbx"]="$FBX_BASE_URL/Idle.fbx"
    ["running.fbx"]="$FBX_BASE_URL/Running.fbx"
    ["jump.fbx"]="$FBX_BASE_URL/Jump.fbx"
)

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)         PORT="$2"; shift 2 ;;
        --ue-port)      UE_PORT="$2"; shift 2 ;;
        --skip-assets)  SKIP_ASSETS=true; shift ;;
        *)              echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# Cleanup
cleanup() {
    echo ""
    echo "  Shutting down..."
    [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null && echo "  Web UI stopped"
    exit 0
}
trap cleanup SIGINT SIGTERM

echo "============================================"
echo "  Instant4D — Unreal Engine 5"
echo "  Multi-Agent 3D Scene Generator"
echo "============================================"

# ─── Auth ──────────────────────────────────────────────────────────────────
if [[ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]] && [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    if [[ -f "$HOME/.claude/.credentials.json" ]]; then
        echo "  Auth: Claude Code credentials detected"
    else
        echo ""
        echo "  WARNING: No authentication configured!"
        echo "  Set ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY, or 'claude login'"
        echo ""
    fi
fi

# ─── Step 0: Download Character Assets ─────────────────────────────────────
IMPORT_DIR="$UE_CONTENT/Import"
if [[ "$SKIP_ASSETS" == false ]]; then
    echo ""
    echo "[0/3] Checking character assets..."
    mkdir -p "$IMPORT_DIR"

    ALL_PRESENT=true
    for fname in "${!FBX_FILES[@]}"; do
        if [[ ! -f "$IMPORT_DIR/$fname" ]] || [[ $(stat -c%s "$IMPORT_DIR/$fname" 2>/dev/null || echo 0) -lt 1000 ]]; then
            ALL_PRESENT=false
            break
        fi
    done

    if [[ "$ALL_PRESENT" == true ]]; then
        echo "  Character FBX files already downloaded."
    else
        echo "  Downloading Mixamo character FBX files..."
        for fname in "${!FBX_FILES[@]}"; do
            url="${FBX_FILES[$fname]}"
            if [[ ! -f "$IMPORT_DIR/$fname" ]] || [[ $(stat -c%s "$IMPORT_DIR/$fname" 2>/dev/null || echo 0) -lt 1000 ]]; then
                echo "    Downloading $fname..."
                curl -sL -o "$IMPORT_DIR/$fname" "$url" --connect-timeout 15 --max-time 120
                size=$(stat -c%s "$IMPORT_DIR/$fname" 2>/dev/null || echo 0)
                if [[ "$size" -lt 1000 ]]; then
                    echo "    WARNING: $fname download may have failed (${size} bytes)"
                else
                    echo "    OK ($(numfmt --to=iec $size))"
                fi
            fi
        done
        echo "  Character assets downloaded to $IMPORT_DIR"
    fi
else
    echo ""
    echo "[0/3] Skipping asset download (--skip-assets)"
fi

# ─── Kill stale web UI only ──────────────────────────────────────────────
lsof -ti:"$PORT" 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 0.5

# ─── Step 1: Connect to Real UE5 ──────────────────────────────────────────
echo ""
echo "[1/3] Connecting to UE5 on port $UE_PORT..."

UE5_JUST_STARTED=false
if curl -s "http://localhost:$UE_PORT/api/health" 2>/dev/null | grep -q '"engine"'; then
    echo "  UE5 already running!"
else
    echo "  UE5 not detected. Launching editor..."

    if [[ ! -f "$UE_EDITOR" ]]; then
        echo "  ERROR: UnrealEditor not found at $UE_EDITOR"
        exit 1
    fi

    mkdir -p "$SCRIPT_DIR/renders"

    nohup "$UE_EDITOR" "$UE_PROJECT" \
        -RenderOffscreen -nosplash -nosound -unattended -log \
        > /tmp/ue5_instant4d.log 2>&1 &
    UE5_PID=$!
    echo "  UE5 PID: $UE5_PID (log: /tmp/ue5_instant4d.log)"

    echo "  Waiting for UE5 API..."
    for i in $(seq 1 120); do
        if curl -s "http://localhost:$UE_PORT/api/health" 2>/dev/null | grep -q '"engine"'; then
            echo "  Real UE5 ready! (${i}s)"
            UE5_JUST_STARTED=true
            break
        fi
        if [[ $i -eq 120 ]]; then
            echo "  ERROR: UE5 did not respond after 120s."
            echo "  Check /tmp/ue5_instant4d.log for errors."
            exit 1
        fi
        sleep 1
    done
fi

# ─── Step 2: Import Character Assets into UE5 ─────────────────────────────
if [[ "$SKIP_ASSETS" == false ]]; then
    echo ""
    echo "[2/3] Importing character assets into UE5..."

    # Check if assets are already imported
    ASSET_CHECK=$(curl -s -X POST "http://localhost:$UE_PORT/api/exec" \
        -H "Content-Type: application/json" \
        -d '{"code": "import unreal\nm = unreal.load_asset(\"/Game/Characters/Mixamo/SK_MixamoCharacter\")\nresult = \"exists\" if m else \"missing\""}' 2>/dev/null)

    if echo "$ASSET_CHECK" | grep -q '"exists"'; then
        echo "  Mixamo character already imported in UE5."
    elif [[ -f "$IMPORT_DIR/standard_walk.fbx" ]]; then
        echo "  Importing character mesh + skeleton + walk animation..."

        # Import main character FBX (mesh + skeleton + walk animation)
        IMPORT_CODE=$(cat <<'PYEOF'
import unreal

def _import_main():
    task = unreal.AssetImportTask()
    task.set_editor_property('filename', 'IMPORT_DIR_PLACEHOLDER/standard_walk.fbx')
    task.set_editor_property('destination_path', '/Game/Characters/Mixamo')
    task.set_editor_property('destination_name', 'SK_MixamoCharacter')
    task.set_editor_property('replace_existing', True)
    task.set_editor_property('automated', True)
    task.set_editor_property('save', True)
    options = unreal.FbxImportUI()
    options.set_editor_property('import_mesh', True)
    options.set_editor_property('import_textures', True)
    options.set_editor_property('import_materials', True)
    options.set_editor_property('import_as_skeletal', True)
    options.set_editor_property('import_animations', True)
    options.set_editor_property('create_physics_asset', False)
    task.set_editor_property('options', options)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    imported = task.get_editor_property('imported_object_paths')
    unreal.log('[Instant4D] Main FBX imported: ' + str([str(o) for o in imported]))
    return [str(o) for o in imported]

result = _run_on_game_thread(_import_main)
PYEOF
)
        IMPORT_CODE="${IMPORT_CODE//IMPORT_DIR_PLACEHOLDER/$IMPORT_DIR}"

        RESULT=$(curl -s -X POST "http://localhost:$UE_PORT/api/exec" \
            -H "Content-Type: application/json" \
            --data-binary "$(python3 -c "import json; print(json.dumps({'code': '''$IMPORT_CODE'''}))")" 2>/dev/null || echo "FAIL")

        if echo "$RESULT" | grep -q '"success": true'; then
            echo "  Character mesh imported."
        else
            echo "  WARNING: Character import may have failed: $RESULT"
        fi

        # Import animation FBXes (idle, running, jump)
        echo "  Importing animations..."
        for anim_pair in "idle.fbx:Anim_Idle" "running.fbx:Anim_Running" "jump.fbx:Anim_Jump"; do
            IFS=':' read -r anim_file anim_name <<< "$anim_pair"
            if [[ -f "$IMPORT_DIR/$anim_file" ]]; then
                ANIM_CODE="import unreal
def _import_anim():
    skeleton = unreal.load_asset('/Game/Characters/Mixamo/SK_MixamoCharacter_Skeleton')
    if not skeleton:
        return 'ERROR: skeleton not found'
    task = unreal.AssetImportTask()
    task.set_editor_property('filename', '$IMPORT_DIR/$anim_file')
    task.set_editor_property('destination_path', '/Game/Characters/Mixamo')
    task.set_editor_property('destination_name', '$anim_name')
    task.set_editor_property('replace_existing', True)
    task.set_editor_property('automated', True)
    task.set_editor_property('save', True)
    options = unreal.FbxImportUI()
    options.set_editor_property('import_mesh', False)
    options.set_editor_property('import_textures', False)
    options.set_editor_property('import_materials', False)
    options.set_editor_property('import_as_skeletal', True)
    options.set_editor_property('import_animations', True)
    options.set_editor_property('skeleton', skeleton)
    options.set_editor_property('mesh_type_to_import', unreal.FBXImportType.FBXIT_ANIMATION)
    task.set_editor_property('options', options)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    imported = task.get_editor_property('imported_object_paths')
    return [str(o) for o in imported]
result = _run_on_game_thread(_import_anim)"

                ANIM_RESULT=$(curl -s -X POST "http://localhost:$UE_PORT/api/exec" \
                    -H "Content-Type: application/json" \
                    --data-binary "$(python3 -c "import json,sys; print(json.dumps({'code': sys.stdin.read()}))" <<< "$ANIM_CODE")" 2>/dev/null || echo "FAIL")

                if echo "$ANIM_RESULT" | grep -q '"success": true'; then
                    echo "    $anim_name imported."
                else
                    echo "    WARNING: $anim_name import may have failed."
                fi
            fi
        done
        echo "  Character asset import complete."
    else
        echo "  WARNING: FBX files not found in $IMPORT_DIR. Character will use Manny fallback."
    fi
else
    echo ""
    echo "[2/3] Skipping asset import (--skip-assets)"
fi

# ─── Step 3: Start Web UI ─────────────────────────────────────────────────
echo ""
echo "[3/3] Starting web UI on port $PORT..."
export UE_API_URL="http://localhost:$UE_PORT"
python web.py &
WEB_PID=$!
echo "  PID: $WEB_PID"
sleep 1

if ! kill -0 "$WEB_PID" 2>/dev/null; then
    echo "  ERROR: Web UI failed to start. Check dependencies: pip install -r requirements.txt"
    exit 1
fi

echo ""
echo "============================================"
echo "  All services running!"
echo ""
echo "  Web UI:     http://localhost:$PORT"
echo "  Scene API:  http://localhost:$UE_PORT"
echo "  Viewport:   Live JPEG stream @ 30fps"
echo ""
echo "  Controls:"
echo "    WASD     - Move camera / walk"
echo "    Mouse    - Look around (click viewport first)"
echo "    Q/E      - Fly up/down"
echo "    Scroll   - Zoom"
echo "    Shift    - Fast movement"
echo "    ESC      - Release mouse"
echo "    Play     - Third-person character mode"
echo ""
echo "  Press Ctrl+C to stop all services"
echo "============================================"

wait "$WEB_PID" 2>/dev/null || true
cleanup
