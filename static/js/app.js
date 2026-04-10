// Instant4D — Unreal Engine 5 Interactive Frontend

// All UE5 API calls go through Flask proxy at /ue5/api/* — no direct port 8000 access needed
const UE_URL = '';  // same origin — proxied through Flask
const API_URL = '';  // same origin

let currentJobId = null;
let eventSource = null;
let captured = false;
let keysDown = new Set();
let controlInterval = null;
let infoInterval = null;

// Control mode: 'camera' (free-fly editor camera) or 'walk' (third-person character)
let controlMode = 'camera';

// ─── Input batching — accumulates mouse + key state, sends ONE request per tick ─
// This prevents the command queue from flooding with dozens of in-flight fetches.

let _mouseDx = 0, _mouseDy = 0;        // accumulated mouse delta since last send
let _inputInFlight = false;              // true while a fetch is pending — gate new sends
let _playerInputInFlight = false;

function _sendBatchedInput() {
    if (!captured) return;

    if (controlMode === 'walk') {
        _sendBatchedPlayerInput();
    } else {
        _sendBatchedCameraInput();
    }
}

function _sendBatchedCameraInput() {
    if (_inputInFlight) return;  // previous request still pending — skip this tick

    const speed = keysDown.has('shift') ? 4.0 : 1.5;
    let dx = 0, dy = 0, dz = 0;
    let dyaw = _mouseDx * 0.3;
    let dpitch = -_mouseDy * 0.3;
    _mouseDx = 0; _mouseDy = 0;

    if (keysDown.has('w')) dx += speed;
    if (keysDown.has('s')) dx -= speed;
    if (keysDown.has('a')) dy -= speed;
    if (keysDown.has('d')) dy += speed;
    if (keysDown.has('q') || keysDown.has(' ')) dz += speed * 0.7;
    if (keysDown.has('e') || keysDown.has('c')) dz -= speed * 0.7;

    // Nothing to send
    if (dx === 0 && dy === 0 && dz === 0 && dyaw === 0 && dpitch === 0) return;

    _inputInFlight = true;
    fetch(UE_URL + '/ue5/api/scene/camera/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: 'batch', dx, dy, dz, dyaw, dpitch, speed }),
    })
    .then(() => { _inputInFlight = false; })
    .catch(() => { _inputInFlight = false; });
}

function _sendBatchedPlayerInput() {
    if (_playerInputInFlight) return;

    const speed = keysDown.has('shift') ? 4.0 : 1.5;
    let forward = 0, right = 0;
    let dyaw = _mouseDx * 0.3;
    _mouseDx = 0; _mouseDy = 0;  // consume mouse delta

    if (keysDown.has('w')) forward += 1;
    if (keysDown.has('s')) forward -= 1;
    if (keysDown.has('a')) right -= 1;
    if (keysDown.has('d')) right += 1;

    // Nothing to send
    if (forward === 0 && right === 0 && dyaw === 0) return;

    _playerInputInFlight = true;
    fetch(UE_URL + '/ue5/api/player/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: 'batch', forward, right, dyaw, speed }),
    })
    .then(() => { _playerInputInFlight = false; })
    .catch(() => { _playerInputInFlight = false; });
}

// ─── Stream (MJPEG from UE5 Python server) ──────────────────────────────────

function initStream() {
    const img = document.getElementById('stream-img');
    const video = document.getElementById('stream-video');
    const placeholder = document.getElementById('stream-placeholder');
    video.style.display = 'none';

    img.onerror = () => {
        img.style.display = 'none';
        placeholder.classList.remove('hidden');
        setTimeout(initStream, 2000);
    };
    img.onload = () => {
        img.style.display = 'block';
        img.style.opacity = '1';
        placeholder.classList.add('hidden');
    };
    // Cache-busting param ensures each tab opens its own stream connection
    img.src = UE_URL + '/ue5/api/stream?_t=' + Date.now() + '_' + Math.random().toString(36).slice(2);
}

// ─── Camera Controls ─────────────────────────────────────────────────────────

function initViewportControls() {
    const viewport = document.getElementById('viewport-3d');

    viewport.addEventListener('click', () => {
        if (!captured) viewport.requestPointerLock();
    });

    document.addEventListener('pointerlockchange', () => {
        if (document.pointerLockElement === viewport) {
            captured = true;
            viewport.classList.add('captured');
            _updateControlsHint(true);
        } else {
            releaseCursor();
        }
    });

    document.addEventListener('keydown', (e) => {
        if (!captured) return;
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
        if (e.key === 'Tab') {
            e.preventDefault();
            toggleControlMode();
            return;
        }
        keysDown.add(e.key.toLowerCase());
        e.preventDefault();
    });

    document.addEventListener('keyup', (e) => {
        keysDown.delete(e.key.toLowerCase());
    });

    // Accumulate mouse deltas — they get consumed by the batched send
    document.addEventListener('mousemove', (e) => {
        if (!captured) return;
        _mouseDx += (e.movementX || 0);
        _mouseDy += (e.movementY || 0);
    });

    viewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        if (controlMode === 'camera') {
            // Zoom can be fire-and-forget (rare event)
            fetch(UE_URL + '/ue5/api/scene/camera/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: 'zoom', delta: -Math.sign(e.deltaY) }),
            }).catch(() => {});
        }
    }, { passive: false });

    // Single batched send per tick — replaces the old per-key individual fetches
    controlInterval = setInterval(_sendBatchedInput, 50);  // 20 Hz — one request per tick max
}

function releaseCursor() {
    captured = false;
    keysDown.clear();
    _mouseDx = 0; _mouseDy = 0;  // discard accumulated deltas
    const vp = document.getElementById('viewport-3d');
    vp.classList.remove('captured');
    if (document.pointerLockElement === vp) document.exitPointerLock();
    _updateControlsHint(false);
}

function _updateControlsHint(isCaptured) {
    const modeLabel = playModeActive ? '[PLAY]' : (controlMode === 'walk' ? '[WALK]' : '[FLY]');
    if (isCaptured) {
        const moveHint = controlMode === 'walk'
            ? 'WASD: Walk | Mouse: Look | Shift: Sprint'
            : 'WASD: Move | Mouse: Look | Q/E: Up/Down | Scroll: Zoom';
        document.getElementById('controls-hint').textContent =
            `${modeLabel} ESC to release | ${moveHint} | TAB: Toggle Mode`;
    } else {
        const extra = playModeActive ? ' | Click to resume' : '';
        document.getElementById('controls-hint').textContent =
            `${modeLabel} Click viewport to enable controls | TAB: Toggle Mode${extra}`;
    }
}

function toggleControlMode() {
    controlMode = controlMode === 'camera' ? 'walk' : 'camera';
    const btn = document.getElementById('btn-mode-toggle');
    if (btn) {
        btn.textContent = controlMode === 'walk' ? 'Walk Mode' : 'Camera Mode';
        btn.className = controlMode === 'walk'
            ? 'btn btn-sm mode-toggle mode-walk'
            : 'btn btn-sm mode-toggle mode-camera';
    }
    _updateControlsHint(captured);

    const playerHud = document.getElementById('hud-player');
    if (playerHud) {
        playerHud.style.display = controlMode === 'walk' ? 'block' : 'none';
    }

    // When entering walk mode, reset the 3P camera to remove any tilt from free-fly
    if (controlMode === 'walk') {
        fetch(UE_URL + '/ue5/api/player/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: 'reset_camera' }),
        }).catch(() => {});
    }

    logEntry(`Switched to ${controlMode === 'walk' ? 'Walk' : 'Camera'} mode`, 'info');
}

function spawnPlayer() {
    return fetch(UE_URL + '/ue5/api/player/spawn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location: [0, 0, 100] }),
    })
    .then(r => r.json())
    .then(r => {
        if (r.success) logEntry('Player character spawned', 'success');
        else logEntry('Spawn player error: ' + (r.error || 'unknown'), 'error');
        return r;
    })
    .catch(err => { logEntry('Spawn player failed: ' + err, 'error'); });
}

// ─── Play Mode (Third-Person Game) ─────────────────────────────────────────

let playModeActive = false;

function enterPlayMode() {
    const btn = document.getElementById('btn-play');

    if (playModeActive) {
        exitPlayMode();
        return;
    }

    btn.textContent = 'Loading...';
    btn.disabled = true;

    fetch(UE_URL + '/ue5/api/player/info')
        .then(r => r.json())
        .then(info => {
            if (info.spawned) {
                _activatePlayMode();
            } else {
                logEntry('Spawning player character...', 'info');
                spawnPlayer().then(() => setTimeout(_activatePlayMode, 300));
            }
        })
        .catch(() => {
            spawnPlayer().then(() => setTimeout(_activatePlayMode, 300));
        });
}

function _activatePlayMode() {
    playModeActive = true;

    if (controlMode !== 'walk') {
        controlMode = 'walk';
        const modeBtn = document.getElementById('btn-mode-toggle');
        if (modeBtn) {
            modeBtn.textContent = 'Walk Mode';
            modeBtn.className = 'btn btn-sm mode-toggle mode-walk';
        }
        const playerHud = document.getElementById('hud-player');
        if (playerHud) playerHud.style.display = 'block';
    }

    // Reset camera to clean 3P view (no residual free-fly tilt)
    fetch(UE_URL + '/ue5/api/player/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: 'reset_camera' }),
    }).catch(() => {});

    const btn = document.getElementById('btn-play');
    btn.textContent = 'Exit Play';
    btn.disabled = false;
    btn.classList.add('active');

    _updateControlsHint(captured);

    const viewport = document.getElementById('viewport-3d');
    if (!captured) {
        viewport.requestPointerLock();
    }

    logEntry('Play mode ON — WASD to walk, Mouse to look, ESC to release cursor', 'success');
}

function exitPlayMode() {
    playModeActive = false;

    controlMode = 'camera';
    const modeBtn = document.getElementById('btn-mode-toggle');
    if (modeBtn) {
        modeBtn.textContent = 'Camera Mode';
        modeBtn.className = 'btn btn-sm mode-toggle mode-camera';
    }
    const playerHud = document.getElementById('hud-player');
    if (playerHud) playerHud.style.display = 'none';

    const btn = document.getElementById('btn-play');
    btn.textContent = 'Play';
    btn.disabled = false;
    btn.classList.remove('active');

    releaseCursor();

    _updateControlsHint(false);
    logEntry('Play mode OFF — switched to free camera', 'info');
}

// ─── HUD / Info ──────────────────────────────────────────────────────────────

function pollSceneInfo() {
    fetch(UE_URL + '/ue5/api/scene/info')
        .then(r => r.json())
        .then(data => {
            const statusEl = document.getElementById('engine-status');
            if (data.connected) {
                statusEl.className = 'status-dot connected';
                statusEl.textContent = 'UE5 Connected';
            } else {
                statusEl.className = 'status-dot disconnected';
                statusEl.textContent = 'UE5 Disconnected';
            }

            document.getElementById('stream-fps').textContent = (data.fps || 0).toFixed(0) + ' FPS';
            document.getElementById('hud-fps').textContent = (data.fps || 0).toFixed(0) + ' FPS';
            document.getElementById('hud-map').textContent = data.map || data.level || '--';

            const cam = data.camera || {};
            const loc = cam.location || [0, 0, 0];
            const rot = cam.rotation || [0, 0, 0];
            document.getElementById('hud-pos').textContent =
                `X: ${loc[0]?.toFixed(0)} Y: ${loc[1]?.toFixed(0)} Z: ${loc[2]?.toFixed(0)} | P: ${rot[0]?.toFixed(0)} Y: ${rot[1]?.toFixed(0)}`;
            document.getElementById('cam-pos').textContent =
                `${loc[0]?.toFixed(0)}, ${loc[1]?.toFixed(0)}, ${loc[2]?.toFixed(0)}`;
            document.getElementById('cam-rot').textContent =
                `P: ${rot[0]?.toFixed(0)} Y: ${rot[1]?.toFixed(0)}`;

            if (!document.activeElement || document.activeElement.tagName !== 'INPUT') {
                document.getElementById('cam-x').value = loc[0]?.toFixed(0);
                document.getElementById('cam-y').value = loc[1]?.toFixed(0);
                document.getElementById('cam-z').value = loc[2]?.toFixed(0);
                document.getElementById('cam-pitch').value = rot[0]?.toFixed(0);
                document.getElementById('cam-yaw').value = rot[1]?.toFixed(0);
            }

            const playerHud = document.getElementById('hud-player');
            if (playerHud && data.player) {
                const pl = data.player.location || [0, 0, 0];
                const py = data.player.yaw || 0;
                playerHud.textContent =
                    `Player: X:${pl[0]?.toFixed(0)} Y:${pl[1]?.toFixed(0)} Z:${pl[2]?.toFixed(0)} Yaw:${py.toFixed(0)}`;
            }

            const infoDiv = document.getElementById('scene-info');
            const playerRow = data.player
                ? `<div class="info-row"><span class="info-label">Player</span><span class="info-value">X:${data.player.location[0]?.toFixed(0)} Y:${data.player.location[1]?.toFixed(0)} Z:${data.player.location[2]?.toFixed(0)}</span></div>`
                : '';
            infoDiv.innerHTML = `
                <div class="info-row"><span class="info-label">Level</span><span class="info-value">${data.map || data.level || '--'}</span></div>
                <div class="info-row"><span class="info-label">Vehicles</span><span class="info-value">${data.vehicles || 0}</span></div>
                <div class="info-row"><span class="info-label">Walkers</span><span class="info-value">${data.walkers || 0}</span></div>
                <div class="info-row"><span class="info-label">Props</span><span class="info-value">${data.props || 0}</span></div>
                <div class="info-row"><span class="info-label">Total</span><span class="info-value">${data.actors_total || 0}</span></div>
                ${playerRow}
            `;
        })
        .catch(() => {
            document.getElementById('engine-status').className = 'status-dot disconnected';
            document.getElementById('engine-status').textContent = 'UE5 Offline';
        });
}

// ─── Tabs ────────────────────────────────────────────────────────────────────

function switchTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    document.querySelectorAll('.tab').forEach(t => {
        if (t.textContent.toLowerCase().includes(name.substring(0, 4))) t.classList.add('active');
    });
    if (name === 'gallery') refreshImages();
}

// ─── Scene Controls ──────────────────────────────────────────────────────────

function setWeather() {
    const preset = document.getElementById('weather-preset').value;
    fetch(UE_URL + '/ue5/api/scene/weather', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset }),
    }).then(() => logEntry(`Weather: ${preset}`, 'success')).catch(() => {});
}

function loadLevel() {
    const level = document.getElementById('level-select').value;
    logEntry(`Loading level ${level}...`, 'info');
    fetch(UE_URL + '/ue5/api/scene/level', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ level: `/Game/Maps/${level}` }),
    }).then(() => logEntry(`Level loaded: ${level}`, 'success')).catch(() => {});
}

function teleportCamera() {
    const loc = [
        parseFloat(document.getElementById('cam-x').value) || 0,
        parseFloat(document.getElementById('cam-y').value) || 0,
        parseFloat(document.getElementById('cam-z').value) || 500,
    ];
    const rot = [
        parseFloat(document.getElementById('cam-pitch').value) || -30,
        parseFloat(document.getElementById('cam-yaw').value) || 0,
        0,
    ];
    fetch(UE_URL + '/ue5/api/scene/camera', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location: loc, rotation: rot }),
    }).then(() => logEntry(`Camera: (${loc.join(', ')})`, 'success')).catch(() => {});
}

function spawnVehicle() {
    const asset = document.getElementById('vehicle-bp').value;
    const loc = [
        parseFloat(document.getElementById('spawn-x').value) || 0,
        parseFloat(document.getElementById('spawn-y').value) || 0,
        0,
    ];
    const rot = [0, parseFloat(document.getElementById('spawn-yaw').value) || 0, 0];

    fetch(UE_URL + '/ue5/api/scene/actors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset, location: loc, rotation: rot }),
    })
    .then(r => r.json())
    .then(r => {
        if (r.success) logEntry(`Spawned ${asset.split('/').pop()} (${r.actor_id})`, 'success');
        else logEntry(`Spawn error: ${r.error}`, 'error');
    })
    .catch(err => logEntry(`Spawn failed: ${err}`, 'error'));
}

function saveSnapshot() {
    fetch(API_URL + '/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'snapshot' }),
    })
    .then(r => r.json())
    .then(r => {
        if (r.success) { logEntry(`Snapshot: ${r.filename}`, 'success'); refreshImages(); }
        else logEntry(`Snapshot error: ${r.error}`, 'error');
    });
}

function clearScene() {
    fetch(UE_URL + '/ue5/api/scene/clear', { method: 'POST' })
        .then(r => r.json())
        .then(r => {
            if (r.success) logEntry(`Scene cleared: ${r.destroyed} actors`, 'success');
            else logEntry(`Clear error: ${r.error}`, 'error');
        });
}

function applyPreset(value) {
    if (value) document.getElementById('prompt').value = value;
}

// ─── Generate Scene ──────────────────────────────────────────────────────────

function generateScene(mode) {
    mode = mode || 'new';
    const prompt = document.getElementById('prompt').value.trim();
    if (!prompt) { logEntry('Enter a scene description.', 'error'); return; }

    const model = document.getElementById('model').value;
    const btnGen = document.getElementById('btn-generate');
    const btnEdit = document.getElementById('btn-edit');
    btnGen.disabled = true;
    btnEdit.disabled = true;
    const active = mode === 'edit' ? btnEdit : btnGen;
    active.innerHTML = '<span class="spinner"></span> ' + (mode === 'edit' ? 'Editing...' : 'Generating...');

    logEntry(`Starting ${mode === 'edit' ? 'edit' : 'new scene'} pipeline...`, 'agent');

    fetch(API_URL + '/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, model, mode, pipeline: 'direct' }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) { logEntry('Error: ' + data.error, 'error'); resetGenBtn(); return; }
        currentJobId = data.job_id;
        logEntry(`Job: ${data.job_id}`, 'info');
        connectSSE(data.job_id);
    })
    .catch(err => { logEntry('Request failed: ' + err, 'error'); resetGenBtn(); });
}

function connectSSE(jobId) {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(API_URL + `/api/events/${jobId}`);
    eventSource.onmessage = (e) => handlePipelineEvent(JSON.parse(e.data));
    eventSource.onerror = () => { logEntry('SSE lost', 'error'); eventSource.close(); resetGenBtn(); };
}

function handlePipelineEvent(ev) {
    const t = ev.type, d = ev.data || {};
    switch (t) {
        case 'pipeline_start':
            const ptype = d.pipeline === 'parallel' ? 'Parallel' : 'Sequential';
            logEntry(`${ptype} Pipeline: "${d.prompt}"`, 'agent');
            break;
        case 'agents_loaded':
            logEntry(`${d.count || (d.agents||[]).length} agents loaded`, 'info');
            break;
        case 'agent_launch':
        case 'agent_launched':
            logEntry(`>> Launched: ${d.agent} (#${d.total_launched || '?'})`, 'agent');
            break;
        case 'text': if (d.message) logEntry(d.message, 'text'); break;
        case 'tool_use':
            if (d.name !== 'Agent') logEntry(`${d.name}: ${d.input_preview || ''}`, 'tool');
            break;
        case 'subagent_start': logEntry(`>> ${d.agent} started`, 'agent'); break;
        case 'subagent_done':
            const cnt = d.completed || d.total_completed || '';
            const total = d.total || '';
            const progress = cnt ? ` (${cnt}/${total})` : '';
            logEntry(`<< Done: ${d.status}${progress}`, 'success');
            break;
        case 'pipeline_complete':
            const agents = d.agents_started || d.agent_starts || 0;
            const agentsDone = d.agents_completed || d.agent_completions || 0;
            logEntry(`Complete! (${(d.elapsed || 0).toFixed(0)}s, ${agentsDone}/${agents} agents)`, 'success');
            resetGenBtn(); refreshImages();
            if (!playModeActive) {
                logEntry('Entering play mode — walk around your scene!', 'info');
                setTimeout(enterPlayMode, 500);
            }
            break;
        case 'error': logEntry(`Error: ${d.message}`, 'error'); resetGenBtn(); break;
        case 'done': if (eventSource) eventSource.close(); resetGenBtn(); refreshImages(); break;
    }
}

function resetGenBtn() {
    const g = document.getElementById('btn-generate');
    const e = document.getElementById('btn-edit');
    g.disabled = false; e.disabled = false;
    g.textContent = 'New Scene'; e.textContent = 'Edit Scene';
}

// ─── Gallery ─────────────────────────────────────────────────────────────────

function refreshImages() {
    fetch(API_URL + '/api/images')
        .then(r => r.json())
        .then(data => {
            const gallery = document.getElementById('image-gallery');
            const imgs = data.images || [];
            if (!imgs.length) {
                gallery.innerHTML = '<p style="color:var(--text-muted);font-size:12px;">No renders yet.</p>';
                return;
            }
            gallery.innerHTML = imgs.map(i =>
                `<img src="/renders/${i}?t=${Date.now()}" alt="${i}" title="${i}" onclick="window.open('/renders/${i}','_blank')">`
            ).join('');
        });
}

// ─── Log ─────────────────────────────────────────────────────────────────────

function logEntry(msg, type = 'info') {
    const log = document.getElementById('pipeline-log');
    const el = document.createElement('div');
    el.className = `log-entry log-${type}`;
    const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
    const pfx = { info:'[i]', text:'[>]', agent:'[A]', tool:'[T]', error:'[!]', success:'[+]', thinking:'[~]' };
    el.textContent = `${ts} ${pfx[type] || '[?]'} ${msg}`;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    while (log.children.length > 200) log.removeChild(log.firstChild);
}

function clearLog() {
    document.getElementById('pipeline-log').innerHTML = '';
}

// ─── Init ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initStream();
    initViewportControls();
    infoInterval = setInterval(pollSceneInfo, 500);
    pollSceneInfo();
    window.addEventListener('blur', releaseCursor);
});
