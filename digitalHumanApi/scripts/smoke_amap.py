#!/usr/bin/env python
"""高德 Web 服务冒烟脚本（批次8 B3：GPS/高德）——「描述位置 → 路线」全流程。

真实 key 验证（验收项）：对配置的 AMAP_WEB_KEY 执行完整链路——
geocode（地理编码，多候选消歧可见）→ geocode 终点 → route（路径规划，
含 distance/duration/steps 指令文本），验证结构化零幻觉（坐标全部来自
高德 API）。

用法（复用部署 .env 的 AMAP_WEB_KEY，也可用环境变量覆盖）:
    python -m scripts.smoke_amap                       # 默认：黄鹤楼 → 武昌火车站（步行）
    python -m scripts.smoke_amap 灵山胜境 灵山景区东门 driving
    python -m scripts.smoke_amap 黄鹤楼 武昌火车站 transit

无 key 时输出 amap_not_configured 错误信封（等价验证错误码契约，退出码 1）。
退出码 0 表示全流程通过。

错误码契约见 doc/amap-gps-api.md；实测记录见 learnings（2026-08-08
地理编码/步行/驾车路径规划已实测可用）。
"""

import asyncio
import json
import sys

from app.services import amap

PASS = 0
FAIL = 1


def _show(name: str, result: dict) -> bool:
    """打印信封并返回是否成功。"""
    print(f"--- {name} ---")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return bool(result.get("ok"))


async def main() -> int:
    address = sys.argv[1] if len(sys.argv) > 1 else "黄鹤楼"
    destination_desc = sys.argv[2] if len(sys.argv) > 2 else "武昌火车站"
    mode = sys.argv[3] if len(sys.argv) > 3 else "walking"

    # 1. 起点地理编码（多候选消歧：同名跨城市时返回候选列表供模型判断）
    origin_geo = await amap.geocode(address)
    if not _show(f"geocode 起点「{address}」", origin_geo):
        return FAIL
    origin = origin_geo["data"]["candidates"][0]["location"]

    # 2. 终点地理编码
    dest_geo = await amap.geocode(destination_desc)
    if not _show(f"geocode 终点「{destination_desc}」", dest_geo):
        return FAIL
    destination = dest_geo["data"]["candidates"][0]["location"]

    # 3. 路径规划（walking/driving/transit；transit 自动推导城市）
    plan = await amap.route(origin, destination, mode)
    if not _show(f"route {mode}（{origin} → {destination}）", plan):
        return FAIL
    data = plan["data"]
    if not data.get("steps"):
        print("SMOKE FAIL: route 无 steps")
        return FAIL
    print(f"[PASS] 全流程完成: {address} → {destination_desc} ({mode}), "
          f"distance={data.get('distance')}m duration={data.get('duration')}s, "
          f"steps={len(data['steps'])}")
    return PASS


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
