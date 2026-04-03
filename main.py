from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from src.caster import CasterCfg, NtripCaster, configure_logging_ex, load_config, validate_config, ConfigValidationError


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="async-ntrip-caster")
    p.add_argument("--config", required=True, help="config/caster_config.json")
    p.add_argument("--check-config", action="store_true")
    return p.parse_args(argv)


async def run_server(cfg: CasterCfg, cfg_path: str) -> None:
    configure_logging_ex(cfg.logging)
    caster = NtripCaster(cfg, cfg_path=cfg_path)
    await caster.start()

    stop_event = asyncio.Event()
    reload_event = asyncio.Event()

    def _stop() -> None:
        stop_event.set()

    def _reload() -> None:
        reload_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    if hasattr(signal, "SIGHUP"):
        try:
            loop.add_signal_handler(signal.SIGHUP, _reload)
        except Exception:
            pass

    async def _watch_config() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(2.0)
            if caster.config_changed():
                reload_event.set()

    watch_task = asyncio.create_task(_watch_config())

    async def _reloader() -> None:
        while not stop_event.is_set():
            await reload_event.wait()
            reload_event.clear()
            await caster.reload_from_disk()

    reload_task = asyncio.create_task(_reloader())

    await stop_event.wait()
    watch_task.cancel()
    reload_task.cancel()
    try:
        await watch_task
    except Exception:
        pass
    try:
        await reload_task
    except Exception:
        pass
    await caster.close()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"CONFIG LOAD ERROR: {e}")
        return 2
    if args.check_config:
        try:
            validate_config(cfg)
            print("OK")
            print(f"listen={cfg.listen.host}:{cfg.listen.port}")
            print(f"users={len(cfg.users)} tiers={len(cfg.tiers)}")
            mps = list(cfg.sources.mountpoints)
            print(f"source_mountpoints={mps}")
            return 0
        except ConfigValidationError as e:
            print("INVALID CONFIG")
            for err in e.errors:
                print(f"- {err}")
            return 2
    try:
        validate_config(cfg)
    except ConfigValidationError as e:
        print("INVALID CONFIG")
        for err in e.errors:
            print(f"- {err}")
        return 2
    asyncio.run(run_server(cfg, args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
