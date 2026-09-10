"""check_frontend_contract.py —— 跨语言契约检查：JS（B 线）产出的帧能否通过 Python 契约（A 线）？

为什么需要这个脚本（这是三线并行最容易被忽略的一环）：
    A 线的契约校验在 backend/contract.py（Python），B 线的离线 mock 在
    frontend/mock.js（JavaScript）。**两套实现，一份契约。** 如果只在各自语言里
    自测，M2 的真实数据传过来时才会暴露"JS 认为合法、Python 认为非法"的字段分歧。

用法（在仓库根执行）：
    node frontend/mock.js --limit 6 > metrics/evidence/js_frames.jsonl
    python metrics/scripts/check_frontend_contract.py metrics/evidence/js_frames.jsonl

    # 管道也可以：node frontend/mock.js --limit 6 | python metrics/scripts/check_frontend_contract.py -

退出码：0 = 全部合法；1 = 有帧不合法或解析失败（可直接进 CI）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，打印 ❌ 之类符号会 UnicodeEncodeError 崩掉脚本本身
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from backend.contract import STATUS_VALUES, validate_frame  # noqa: E402


def _decode(raw: bytes) -> str:
    """容忍 BOM 与 UTF-16。

    实测坑：Windows PowerShell 的 `>` 重定向会把子进程的 UTF-8 输出**重新编码成
    UTF-16LE**，于是 Python 侧 utf-8 解码直接炸（byte 0xff）。团队成员用
    `node ... > file` 是常态，所以这里必须自己认出来，而不是让用户去猜编码。
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-16", errors="replace")


def load_lines(path: str) -> list[dict]:
    if path == "-":
        text = _decode(sys.stdin.buffer.read())
    else:
        p = Path(path)
        if not p.exists():
            raise SystemExit(f"找不到文件：{p}\n先跑：node frontend/mock.js --limit 6 > {path}")
        text = _decode(p.read_bytes())
    frames = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            frames.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"第 {i} 行不是合法 JSON：{exc}") from exc
    return frames


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    frames = load_lines(argv[1])
    if not frames:
        print("❌ 没有读到任何帧（前端 mock 是不是没输出？）")
        return 1

    print(f"读取 {len(frames)} 帧（来源：{argv[1]}）")
    bad = 0
    seen: set[str] = set()
    for fr in frames:
        seen.add(str(fr.get("status")))
        errs = validate_frame(fr)
        if errs:
            bad += 1
            print(f"  ❌ frame_id={fr.get('frame_id')} status={fr.get('status')}")
            for e in errs:
                print(f"       - {e}")

    print(f"状态覆盖：{sorted(s for s in seen if s != 'None')}（契约共 {len(STATUS_VALUES)} 值）")
    if bad:
        print(f"\n跨语言契约检查：失败 —— {bad}/{len(frames)} 帧被 Python 判定为不合法。")
        print("  修法：以 docs/interface.md 为准，改 frontend/mock.js（不是改 contract.py）。")
        return 1
    print(f"\n跨语言契约检查：通过 —— 全部 {len(frames)} 帧在 Python 侧同样合法。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
