"""Minimal programmatic example.

python examples/quickstart/run.py            # offline mock backend, no API key needed
python examples/quickstart/run.py --real     # real Claude calls (needs ANTHROPIC_API_KEY)
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from orchestra.backends import create_backend
from orchestra.engine import Engine
from orchestra.settings import Settings
from orchestra.spec import load_config
from orchestra.store import Store

HERE = Path(__file__).parent


async def main(real: bool) -> None:
    data_dir = tempfile.mkdtemp(prefix="orchestra-example-")
    settings = Settings(
        backend="claude" if real else "mock",
        workspace=str(HERE.parents[1]),  # let the agents read this repository
        data_dir=data_dir,
    )
    engine = Engine(
        load_config(HERE / "orchestra.yaml"),
        settings,
        Store(settings.db_path),
        create_backend(settings),
    )
    run = engine.create_run("Explain what this repository does")
    run = await engine.execute(run.id)

    print(f"status: {run.status.value}")
    for step in engine.store.list_steps(run.id):
        print(f"\n=== {step.key} ({step.agent}) ===\n{(step.output or '').strip()}")


if __name__ == "__main__":
    asyncio.run(main(real="--real" in sys.argv))
