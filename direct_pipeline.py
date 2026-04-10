"""Direct-spawn pipeline — agents spawn objects in real-time via curl.

Architecture: MasterPlanner (1) → 4 ZoneBuilders (parallel, each spawns via curl)
Total: 5 agents. Objects appear in viewport immediately.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskStartedMessage,
    TaskProgressMessage,
    TaskNotificationMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from config import PipelineConfig, get_claude_auth_env, PROJECT_ROOT, WORKSPACE_DIR
from agents.direct_spawn_agents import (
    build_direct_spawn_agents,
    DIRECT_SPAWN_ORCHESTRATOR,
)

log = logging.getLogger(__name__)


class PipelineEvent:
    """Event emitted during pipeline execution."""
    def __init__(self, event_type: str, data: dict):
        self.event_type = event_type
        self.data = data
        self.timestamp = time.time()

    def to_dict(self):
        return {"type": self.event_type, "data": self.data, "ts": self.timestamp}


async def run_direct_pipeline(
    user_prompt: str,
    config: PipelineConfig | None = None,
    event_callback=None,
    model: str | None = None,
    mode: str = "new",
    scene_context: dict | None = None,
) -> dict:
    """Run the direct-spawn pipeline. Objects appear in UE5 in real-time."""
    if config is None:
        config = PipelineConfig()
    if scene_context is None:
        scene_context = {}

    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    config.renders_dir.mkdir(parents=True, exist_ok=True)

    # Clean workspace
    for f in config.workspace_dir.glob("master_plan.json"):
        f.unlink(missing_ok=True)

    def emit(event_type, data):
        ev = PipelineEvent(event_type, data)
        if event_callback:
            event_callback(ev)

    emit("pipeline_start", {"prompt": user_prompt, "mode": mode, "pipeline": "direct"})

    agents = build_direct_spawn_agents(config)
    emit("agents_loaded", {"agents": list(agents.keys()), "count": len(agents)})

    orchestrator_prompt = DIRECT_SPAWN_ORCHESTRATOR.format(
        user_prompt=user_prompt,
        mode=mode,
    )

    auth_env = get_claude_auth_env()
    emit("orchestrator_start", {"model": model or "sonnet", "agent_count": len(agents)})

    start_time = time.time()
    result_text = ""
    total_input_tokens = 0
    total_output_tokens = 0
    agents_started = 0
    agents_completed = 0

    try:
        async for message in query(
            prompt=orchestrator_prompt,
            options=ClaudeAgentOptions(
                model=model or "sonnet",
                cwd=str(PROJECT_ROOT),
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents=agents,
                permission_mode="acceptEdits",
                max_turns=60,
                env=auth_env,
            ),
        ):
            if isinstance(message, ResultMessage):
                result_text = getattr(message, "result", "") or ""
                emit("pipeline_complete", {
                    "result": result_text,
                    "elapsed": time.time() - start_time,
                    "agents_started": agents_started,
                    "agents_completed": agents_completed,
                })

            elif isinstance(message, SystemMessage):
                emit("system", {"session_id": getattr(message, "session_id", None)})

            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        emit("text", {"message": block.text})
                    elif isinstance(block, ToolUseBlock):
                        if block.name == "Agent":
                            agents_started += 1
                            desc = block.input.get("description", "") if isinstance(block.input, dict) else ""
                            emit("agent_launched", {"agent": desc, "total_launched": agents_started})
                        emit("tool_use", {"name": block.name, "input_preview": _summarize(block.input)})
                    elif isinstance(block, ThinkingBlock):
                        emit("thinking", {"preview": block.thinking[:200]})

            elif isinstance(message, TaskStartedMessage):
                emit("subagent_start", {"agent": getattr(message, "agent_type", "unknown")})

            elif isinstance(message, TaskProgressMessage):
                usage = getattr(message, "usage", None)
                if usage:
                    total_input_tokens += getattr(usage, "input_tokens", 0)
                    total_output_tokens += getattr(usage, "output_tokens", 0)

            elif isinstance(message, TaskNotificationMessage):
                agents_completed += 1
                status = getattr(message, "status", None)
                status_val = status.value if hasattr(status, "value") else str(status)
                emit("subagent_done", {"status": status_val, "completed": agents_completed, "total": agents_started})

    except Exception as e:
        log.exception("Direct pipeline failed")
        emit("error", {"message": str(e)})
        return {"success": False, "error": str(e), "elapsed": time.time() - start_time, "pipeline": "direct"}

    elapsed = time.time() - start_time
    images = sorted(str(f.relative_to(PROJECT_ROOT)) for f in config.renders_dir.glob("*.png") if not f.name.startswith("_"))

    return {
        "success": True,
        "pipeline": "direct",
        "result": result_text,
        "images": images,
        "elapsed": elapsed,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "agents_started": agents_started,
        "agents_completed": agents_completed,
    }


def _summarize(inp):
    if isinstance(inp, dict):
        for k in ("prompt", "command", "file_path", "description"):
            if k in inp:
                return str(inp[k])[:100]
    return str(inp)[:100]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    prompt = " ".join(sys.argv[1:]) or "A vibrant city intersection at sunset with taxis, trees, street lamps, and pedestrians"

    async def main():
        def cb(ev):
            t, d = ev.event_type, ev.data
            if t == "pipeline_start": print(f"\n{'='*60}\n  Direct-Spawn Pipeline: {d['prompt']}\n{'='*60}")
            elif t == "agent_launched": print(f"  >> {d['agent']} (#{d['total_launched']})")
            elif t == "subagent_done": print(f"  << Done ({d['completed']}/{d['total']})")
            elif t == "text" and d.get("message", "").strip(): print(f"  {d['message'][:150]}")
            elif t == "pipeline_complete": print(f"\n  Complete! {d['elapsed']:.0f}s, {d['agents_completed']}/{d['agents_started']} agents")
            elif t == "error": print(f"  ERROR: {d['message']}")

        r = await run_direct_pipeline(prompt, event_callback=cb)
        print(f"\n  Success: {r['success']}, Images: {len(r.get('images', []))}")

    asyncio.run(main())
