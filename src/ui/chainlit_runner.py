"""Stable project launcher for the Chainlit frontend."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    """Run Chainlit without inheriting its ambiguous generic DEBUG variable."""
    os.environ.pop("DEBUG", None)

    # Chainlit imports nest_asyncio and patches asyncio globally. That patch is
    # unnecessary for this standalone launcher and prevents AnyIO from finding
    # the active event loop on Python 3.14 (breaking static files and responses).
    # Disable only the import-time patch; Chainlit still starts its own loop.
    import nest_asyncio

    nest_asyncio.apply = lambda *args, **kwargs: None

    # Prefer the standard asyncio loop on Python 3.14. Keep this compatibility
    # adjustment local to the Chainlit launcher.
    from uvicorn import config as uvicorn_config

    uvicorn_config.LOOP_FACTORIES["auto"] = (
        "uvicorn.loops.asyncio:asyncio_loop_factory"
    )

    from chainlit.cli import cli

    app_path = Path(__file__).with_name("chainlit_app.py")
    cli.main(
        args=["run", str(app_path), *sys.argv[1:]],
        prog_name="agent-harness-ui",
    )


if __name__ == "__main__":
    main()
