"""contract.py —— 指标 JSON 契约的唯一来源（A 线 / B 线共用）。

依据：
  - docs/interface.md  第 1 节（A→B 指标 JSON schema）+ 第 2 节（status 6 值枚举）
  - docs/02_三人分工与三线并行开发计划.md  4.1

纪律：
  - 字段名、枚举值**只允许在本文件出现一次**。A 线真实输出与 B 线 Mock 都必须经过
    new_frame() / validate_frame()，否则集成时必然返工（《02》第七节风险表第 2、4 条）。
  - 改字段 = 先改 docs/interface.md 的变更记录，再改本文件的 CONTRACT_VERSION。

只用标准库，任何解释器都能 import。
"""

from __future__ import annotations

import json

try:
    from .console import enable_utf8_console
except ImportError:  # 直接以脚本方式运行
    from console import enable_utf8_console

# 幂等的终端设置：contract.py 被 mock / storage / run_pipeline 等全部模块 import，
# 放在这里可以保证**任何入口**都不会因为控制台编码问题崩掉（见 console.py 的说明）。
enable_utf8_console()

# 与 docs/interface.md 同步；契约冻结为 v1.0 时这里一起改
CONTRACT_VERSION = "v0.91"

# ---- status 枚举（docs/interface.md 第 2 节，仅此 6 值，不得扩展）-----------
STATUS_VALUES: tuple[str, ...] = (
    "normal",          # 各项在正常范围、信号质量良好
    "fatigue_risk",    # 疲劳风险升高（PERCLOS / 长闭眼 / 哈欠）
    "adjust_posture",  # 人脸可见率不足 / 姿态越界
    "unreliable",      # 信号质量门控不通过（光照 / 运动 / 波形稳定性）
    "disconnected",    # 视频源或连接中断（B 线兜底）
    "done",            # 测量结束
)

STATUS_ZH: dict[str, str] = {
    "normal": "正常",
    "fatigue_risk": "疲劳风险升高",
    "adjust_posture": "请调整姿势",
    "unreliable": "信号不可靠",
    "disconnected": "连接中断",
    "done": "测量完成",
}

# 眨眼状态机状态（《02》A3：OPEN→CLOSING→CLOSED→OPEN）
BLINK_STATES: tuple[str, ...] = ("OPEN", "CLOSING", "CLOSED", "OPENING")

# vital 字段允许为 null —— 前端必须优雅处理"暂无心率"（契约硬要求）
VITAL_KEYS: tuple[str, ...] = ("hr_bpm", "hr_conf", "rr_per_min", "rr_conf")

_TOP_KEYS = ("ts", "frame_id", "face", "behavior", "vital", "quality", "status", "advice", "reason")


def new_frame(
    *,
    ts: float,
    frame_id: int,
    face: dict,
    behavior: dict,
    quality: dict,
    status: str,
    advice: str,
    reason: str,
    vital: dict | None = None,
) -> dict:
    """构造一帧契约 JSON。键顺序固定，便于 diff 与人工阅读。"""
    vit = {k: None for k in VITAL_KEYS}
    if vital:
        unknown = set(vital) - set(VITAL_KEYS)
        if unknown:
            raise ValueError(f"vital 出现契约外字段：{sorted(unknown)}（契约只允许 {VITAL_KEYS}）")
        vit.update(vital)
    return {
        "ts": ts,
        "frame_id": frame_id,
        "face": face,
        "behavior": behavior,
        "vital": vit,
        "quality": quality,
        "status": status,
        "advice": advice,
        "reason": reason,
    }


def validate_frame(obj: dict) -> list[str]:
    """校验一帧是否符合契约。返回错误列表，空列表 = 通过。

    刻意不依赖 jsonschema：契约很小，手写校验能让报错信息直接指出"哪个字段错在哪"，
    且保证在只有标准库的解释器里也能跑（CI / 干净机器复现）。
    """
    errors: list[str] = []

    if not isinstance(obj, dict):
        return [f"顶层必须是 object，实际是 {type(obj).__name__}"]

    missing = [k for k in _TOP_KEYS if k not in obj]
    if missing:
        errors.append(f"缺少顶层字段：{missing}")
    extra = [k for k in obj if k not in _TOP_KEYS]
    if extra:
        errors.append(f"出现契约外顶层字段：{extra}（契约不改就不许加字段）")

    if "ts" in obj and not isinstance(obj["ts"], (int, float)):
        errors.append(f"ts 必须是数字，实际是 {type(obj['ts']).__name__}")
    if "frame_id" in obj and not isinstance(obj["frame_id"], int):
        errors.append(f"frame_id 必须是整数，实际是 {type(obj['frame_id']).__name__}")

    # ---- face ----
    face = obj.get("face")
    if not isinstance(face, dict):
        errors.append("face 必须是 object")
    else:
        if set(face) != {"visible", "bbox", "pose"}:
            errors.append(f"face 字段应恰为 visible/bbox/pose，实际 {sorted(face)}")
        vis = face.get("visible")
        if not isinstance(vis, (int, float)) or not (0.0 <= float(vis) <= 1.0):
            errors.append(f"face.visible 必须是 0~1 的数，实际 {vis!r}")
        bbox = face.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, int) for v in bbox)):
            errors.append(f"face.bbox 必须是 4 个整数的数组 [x, y, w, h]，实际 {bbox!r}")
        pose = face.get("pose")
        if not isinstance(pose, dict) or set(pose) != {"yaw", "pitch", "roll"}:
            errors.append(f"face.pose 字段应恰为 yaw/pitch/roll，实际 {pose!r}")
        elif not all(isinstance(pose[k], (int, float)) for k in pose):
            errors.append("face.pose 的 yaw/pitch/roll 必须都是数字")

    # ---- behavior ----
    beh = obj.get("behavior")
    need_beh = {
        "ear_left", "ear_right", "blink_state", "blink_count", "blink_rate_per_min",
        "perclos", "long_close_count", "mar", "yawn_count",
    }
    if not isinstance(beh, dict):
        errors.append("behavior 必须是 object")
    else:
        if set(beh) != need_beh:
            errors.append(
                f"behavior 字段不符：缺少 {sorted(need_beh - set(beh))}，多出 {sorted(set(beh) - need_beh)}"
            )
        if beh.get("blink_state") not in BLINK_STATES:
            errors.append(f"behavior.blink_state 必须是 {BLINK_STATES} 之一，实际 {beh.get('blink_state')!r}")

    # ---- vital：允许 null，但键必须齐全 ----
    vit = obj.get("vital")
    if not isinstance(vit, dict):
        errors.append("vital 必须是 object（没有数据时用 null 填充，不要省略键）")
    else:
        if set(vit) != set(VITAL_KEYS):
            errors.append(f"vital 字段应恰为 {VITAL_KEYS}，实际 {sorted(vit)}")
        for k in ("hr_conf", "rr_conf"):
            v = vit.get(k)
            if isinstance(v, (int, float)) and not (0.0 <= float(v) <= 1.0):
                errors.append(f"vital.{k} 是置信度，必须在 0~1，实际 {v!r}")

    # ---- quality ----
    q = obj.get("quality")
    if not isinstance(q, dict) or set(q) != {"light_score", "motion_score", "overall"}:
        errors.append(f"quality 字段应恰为 light_score/motion_score/overall，实际 {q!r}")
    else:
        for k, v in q.items():
            if not isinstance(v, (int, float)) or not (0.0 <= float(v) <= 1.0):
                errors.append(f"quality.{k} 必须在 0~1，实际 {v!r}")

    # ---- status / advice / reason ----
    st = obj.get("status")
    if st not in STATUS_VALUES:
        errors.append(f"status 必须是 {STATUS_VALUES} 之一，实际 {st!r}")
    for k in ("advice", "reason"):
        v = obj.get(k)
        if not isinstance(v, str) or not v.strip():
            errors.append(f"{k} 必须是非空字符串，实际 {v!r}")

    return errors


def dumps(obj: dict) -> str:
    """单行 JSON（WebSocket 每帧一行 / CSV 旁路日志用）。ensure_ascii=False 保留中文。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def assert_valid(obj: dict) -> dict:
    """校验失败直接抛异常 —— 给"不想静默产出坏数据"的调用方用。"""
    errs = validate_frame(obj)
    if errs:
        raise ValueError("契约校验失败：\n  - " + "\n  - ".join(errs))
    return obj
