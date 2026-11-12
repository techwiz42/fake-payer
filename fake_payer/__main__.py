"""Run the line:  python -m fake_payer   (configuration from the environment; a .env file in the working directory is read for anything not already set)."""
import asyncio
import logging
import os
from pathlib import Path

from .config import Config, ConfigError
from .server import FakePayerServer


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


async def _run(config: Config) -> None:
    server = FakePayerServer(config)
    async with server.serve() as running:
        logging.getLogger("fake_payer").info("listening on %s:%s", config.listen_host, config.port)
        await running.serve_forever()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv(Path(".env"))
    try:
        config = Config.from_env()
    except ConfigError as error:
        print(f"cannot start: {error}")
        return 2
    asyncio.run(_run(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
