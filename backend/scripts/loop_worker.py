"""Loop 后台常驻 Worker：周期性消费反馈并执行 Loop 循环。

用于 K8s 中独立部署 Loop Engine 服务。
用法：python -m scripts.loop_worker [--interval 300]
"""
from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.deps import build_container
from app.utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def main(interval: int) -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    container = build_container(settings)
    if container.mongo is not None:
        await container.mongo.connect()
    if hasattr(container.session_store, "connect"):
        try:
            await container.session_store.connect()
        except Exception:
            pass

    logger.info("Loop Worker 启动，间隔 %ss", interval)
    while True:
        try:
            report = await container.loop_engine.run_cycle()
            if report.get("observed"):
                logger.info("Loop 循环: %s", report)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Loop 循环异常: %s", exc)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    asyncio.run(main(args.interval))
