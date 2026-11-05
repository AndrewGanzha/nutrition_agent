"""Manual smoke run against the real DeepSeek and Google Health APIs.

Not part of CI: it needs live keys and, for the photo step, a real meal photo.

Required env:
    DEEPSEEK_API_KEY

Optional env:
    SMOKE_IMAGE            path to a meal photo (image step is skipped without it)
    SMOKE_USER_ID          user_id passed to the agent (default: None — anonymous)
    NUTRITION_DB           SQLite path (default: smoke.db)
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
                           enable the Google Health sink and its live write

Run from the repository root:

    DEEPSEEK_API_KEY=... uv run python scripts/smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from nutrition_agent import (
    AgentConfig,
    GoogleHealthSink,
    LLMConfig,
    LocalSQLiteSink,
    NutritionAgent,
    OpenAICompatibleLLM,
    ProcessResult,
    Sink,
)

# `examples/` is not an installed package: make it importable when the script is
# run as `python scripts/smoke.py` from the repository root.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEXT = "Съел две котлеты и гречку 200г"


def _report(label: str, result: ProcessResult) -> bool:
    print(f"[{label}] status={result.status}")
    if result.error:
        print(f"[{label}] error={result.error}")
    for sink_result in result.sink_results:
        statuses = ", ".join(
            f"{item.index}:{item.status}" + (f" ({item.error})" if item.error else "")
            for item in sink_result.items
        )
        print(f"[{label}] {sink_result.name}: {statuses}")
    if result.entry is not None:
        items = ", ".join(f"{item.name} {item.grams or '?'}г" for item in result.entry.items)
        print(f"[{label}] items={items}")
    return result.status == "ok"


def _google_sink(config: AgentConfig, user_id: str | None) -> GoogleHealthSink | None:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        print("SKIP google: GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET/GOOGLE_REFRESH_TOKEN not set")
        return None

    from examples.google_token_provider import RefreshTokenProvider

    provider = RefreshTokenProvider(
        client_id=client_id,
        client_secret=client_secret,
        refresh_tokens={user_id: refresh_token},
    )
    return GoogleHealthSink(
        config.sqlite_path,
        token_provider=provider,
        meal_duration=config.meal_duration,
        timeout=config.timeouts.google,
        sqlite_timeout=config.timeouts.sqlite,
    )


async def main() -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("DEEPSEEK_API_KEY is required")
        return 2

    user_id = os.environ.get("SMOKE_USER_ID")
    config = AgentConfig(
        llm=LLMConfig(api_key=api_key),
        sqlite_path=Path(os.environ.get("NUTRITION_DB", "smoke.db")),
    )
    sinks: list[Sink] = [LocalSQLiteSink(config.sqlite_path, timeout=config.timeouts.sqlite)]
    google = _google_sink(config, user_id)
    if google is not None:
        sinks.append(google)

    llm = OpenAICompatibleLLM(config.llm, timeout=config.timeouts.llm, retry=config.retry.llm)
    agent = NutritionAgent(config, llm=llm, sinks=sinks)
    checks: list[bool] = []

    text_result = await agent.process(text=TEXT, user_id=user_id)
    checks.append(_report("text", text_result))
    checks.append(text_result.entry is not None and len(text_result.entry.items) >= 2)

    image_path = os.environ.get("SMOKE_IMAGE")
    if image_path:
        try:
            image_bytes = await asyncio.to_thread(Path(image_path).read_bytes)
        except OSError as exc:
            print(f"FAIL image: cannot read {image_path}: {exc}")
            checks.append(False)
        else:
            image_result = await agent.process(image_bytes=image_bytes, user_id=user_id)
            checks.append(_report("image", image_result))
            checks.append(image_result.entry is not None and bool(image_result.entry.items))
    else:
        print("SKIP image: SMOKE_IMAGE is not set")

    preview = await agent.process(text=TEXT, dry_run=True, user_id=user_id)
    checks.append(_report("dry-run", preview))
    if preview.commit_token is None:
        checks.append(False)
    else:
        first = await agent.commit(preview.commit_token)
        checks.append(_report("commit", first))
        second = await agent.commit(preview.commit_token)
        _report("commit-replay", second)
        # A partial/error commit keeps the token on purpose; only a burned token
        # (full success) must reject the replay.
        checks.append(first.status != "ok" or second.status == "error")

    failed = checks.count(False)
    print(f"\n{'FAIL' if failed else 'OK'}: {len(checks) - failed}/{len(checks)} checks passed")
    if google is not None and failed == 0:
        print("Check the Google Health test-user account for the nutrition-log entries.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
