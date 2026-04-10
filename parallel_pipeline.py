"""Hierarchical parallel pipeline for UE5 3D city-block generation.

This pipeline uses a 3-tier agent hierarchy:
  MasterArchitect -> 4x ZoneDirector (parallel) -> 16x BuilderWorker (parallel)

Based on pipeline.py but restructured for parallel execution.  The orchestrator
prompt instructs Claude to launch agents in parallel using multiple Agent tool
calls in a single message.
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

from config import PipelineConfig, get_claude_auth_env, PROJECT_ROOT
from agents.parallel_agents import (
    build_parallel_agent_definitions,
    PARALLEL_ORCHESTRATOR_PROMPT,
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


def _ensure_dirs(config: PipelineConfig):
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    config.renders_dir.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "assets").mkdir(parents=True, exist_ok=True)


def _clean_workspace(config: PipelineConfig):
    """Remove stale files from previous runs so agents start fresh."""
    patterns = [
        "master_plan.json",
        "zone_*_plan.json",
        "worker_*_commands.json",
        "scene_commands.json",
        "render_result.json",
    ]
    for pattern in patterns:
        for f in config.workspace_dir.glob(pattern):
            f.unlink(missing_ok=True)


async def run_parallel_pipeline(
    user_prompt: str,
    config: PipelineConfig | None = None,
    event_callback=None,
    model: str | None = None,
    mode: str = "new",
    scene_context: dict | None = None,
) -> dict:
    """Run the hierarchical parallel scene generation pipeline.

    Returns dict with pipeline results.
    """
    if config is None:
        config = PipelineConfig()
    if scene_context is None:
        scene_context = {}

    _ensure_dirs(config)
    _clean_workspace(config)

    def emit(event_type, data):
        ev = PipelineEvent(event_type, data)
        if event_callback:
            event_callback(ev)
        return ev

    emit("pipeline_start", {
        "prompt": user_prompt,
        "mode": mode,
        "pipeline": "parallel",
    })

    # Build all 22 agent definitions (1 architect + 4 directors + 16 workers + 1 renderer)
    agents = build_parallel_agent_definitions(config)
    emit("agents_loaded", {
        "agents": list(agents.keys()),
        "count": len(agents),
    })

    # Format the orchestrator prompt
    ctx_str = json.dumps(scene_context, indent=2) if scene_context else "No scene context available."
    orchestrator_prompt = PARALLEL_ORCHESTRATOR_PROMPT.format(
        user_prompt=user_prompt,
        mode=mode,
        scene_context=ctx_str,
        project_root=str(PROJECT_ROOT),
    )

    auth_env = get_claude_auth_env()
    emit("orchestrator_start", {"model": model or "default"})

    start_time = time.time()
    result_text = ""
    total_input_tokens = 0
    total_output_tokens = 0
    agent_starts = 0
    agent_completions = 0

    try:
        async for message in query(
            prompt=orchestrator_prompt,
            options=ClaudeAgentOptions(
                model=model,
                cwd=str(PROJECT_ROOT),
                allowed_tools=["Read", "Write", "Bash", "Glob", "Grep", "Agent"],
                agents=agents,
                permission_mode="acceptEdits",
                max_turns=120,  # Higher limit for 22 agents
                env=auth_env,
            ),
        ):
            if isinstance(message, ResultMessage):
                result_text = getattr(message, "result", "") or ""
                emit(
                    "pipeline_complete",
                    {
                        "result": result_text,
                        "elapsed": time.time() - start_time,
                        "agent_starts": agent_starts,
                        "agent_completions": agent_completions,
                    },
                )

            elif isinstance(message, SystemMessage):
                session_id = getattr(message, "session_id", None)
                emit("system", {"session_id": session_id})

            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        emit("text", {"message": block.text})
                    elif isinstance(block, ToolUseBlock):
                        tool_name = block.name
                        if tool_name == "Agent":
                            # Track which agent is being launched
                            agent_name = block.input.get("agent", "unknown") if isinstance(block.input, dict) else "unknown"
                            emit("agent_launch", {"agent": agent_name})
                        else:
                            emit(
                                "tool_use",
                                {
                                    "name": tool_name,
                                    "input_preview": _summarize_input(block.input),
                                },
                            )
                    elif isinstance(block, ThinkingBlock):
                        emit("thinking", {"preview": block.thinking[:200]})

            elif isinstance(message, TaskStartedMessage):
                agent_type = getattr(message, "agent_type", "unknown")
                agent_starts += 1
                emit("subagent_start", {
                    "agent": agent_type,
                    "total_started": agent_starts,
                })

            elif isinstance(message, TaskProgressMessage):
                usage = getattr(message, "usage", None)
                if usage:
                    inp = getattr(usage, "input_tokens", 0)
                    out = getattr(usage, "output_tokens", 0)
                    total_input_tokens += inp
                    total_output_tokens += out
                    emit("subagent_progress", {
                        "input_tokens": inp,
                        "output_tokens": out,
                    })

            elif isinstance(message, TaskNotificationMessage):
                status = getattr(message, "status", None)
                status_val = status.value if hasattr(status, "value") else str(status)
                agent_completions += 1
                emit("subagent_done", {
                    "status": status_val,
                    "total_completed": agent_completions,
                })

    except Exception as e:
        log.exception("Parallel pipeline failed")
        emit("error", {"message": str(e)})
        return {
            "success": False,
            "error": str(e),
            "elapsed": time.time() - start_time,
            "pipeline": "parallel",
        }

    elapsed = time.time() - start_time
    images = []
    if config.renders_dir.exists():
        images = sorted(
            str(f.relative_to(PROJECT_ROOT))
            for f in config.renders_dir.glob("*.png")
        )

    return {
        "success": True,
        "result": result_text,
        "images": images,
        "elapsed": elapsed,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "agent_starts": agent_starts,
        "agent_completions": agent_completions,
        "pipeline": "parallel",
    }


def _summarize_input(inp):
    if isinstance(inp, dict):
        if "prompt" in inp:
            return inp["prompt"][:120] + "..."
        if "command" in inp:
            return inp["command"][:120]
        if "file_path" in inp:
            return inp["file_path"]
    return str(inp)[:120]


async def run_cli(prompt: str, model: str | None = None):
    """Run the parallel pipeline from CLI with console output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    active_agents: set[str] = set()

    def console_cb(event):
        t = event.event_type
        d = event.data
        if t == "pipeline_start":
            print(f"\n{'=' * 70}")
            print(f"  Instant4D -- Hierarchical Parallel Pipeline")
            print(f"  Prompt: {d['prompt']}")
            print(f"  Mode:   {d['mode']}")
            print(f"{'=' * 70}\n")
        elif t == "agents_loaded":
            print(f"  Registered {d['count']} agents: {', '.join(d['agents'][:5])}...")
        elif t == "orchestrator_start":
            print(f"\n  Starting orchestrator (model: {d['model']})...")
        elif t == "text":
            msg = d["message"][:200]
            if msg.strip():
                print(f"  {msg}")
        elif t == "agent_launch":
            agent = d["agent"]
            active_agents.add(agent)
            print(f"  >> Launching: {agent}  (active: {len(active_agents)})")
        elif t == "subagent_start":
            agent = d["agent"]
            total = d.get("total_started", "?")
            print(f"  >> Agent started: {agent}  (#{total})")
        elif t == "subagent_done":
            total = d.get("total_completed", "?")
            print(f"  << Agent done: {d['status']}  (completed: {total})")
        elif t == "tool_use":
            print(f"     Tool: {d['name']} -- {d.get('input_preview', '')[:80]}")
        elif t == "pipeline_complete":
            agents_run = d.get("agent_completions", "?")
            print(f"\n{'=' * 70}")
            print(f"  Pipeline complete! ({d['elapsed']:.1f}s, {agents_run} agents ran)")
            print(f"{'=' * 70}")
        elif t == "error":
            print(f"\n  ERROR: {d['message']}")

    result = await run_parallel_pipeline(
        prompt,
        event_callback=console_cb,
        model=model,
        mode="new",
    )

    if result["success"]:
        print(f"\n  Images: {result.get('images', [])}")
        print(f"  Tokens: {result.get('total_input_tokens', 0)} in / {result.get('total_output_tokens', 0)} out")
        print(f"  Agents: {result.get('agent_starts', 0)} started, {result.get('agent_completions', 0)} completed")
    else:
        print(f"\n  FAILED: {result.get('error', 'unknown')}")

    return result


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or (
        "A vibrant city block with a commercial downtown in the northwest, "
        "residential suburbs in the northeast, a park in the southwest, "
        "and a mixed-use district in the southeast, at golden hour sunset"
    )
    asyncio.run(run_cli(prompt))
