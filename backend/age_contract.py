"""age_contract.py —— **年龄档案件（age sidecar）** 的唯一 schema 来源。

> 为什么单独开一份"契约"，而不是往 `contract.py` 里加字段：
> `docs/interface.md` 第 1 节的指标帧被冻结为**恰好 9 个顶层字段**，多一个字段就是
> 非法帧（`validate_frame` 会拒收，见 `backend/contract.py`）。年龄档案是
> "人群基线与个体推断"这种**附加解释**，与 `triggers`（判定证据链）、旁路画面
> （`/api/frame` → `/video.mjpg`）属于同一个范式：**走旁路，不污染冻结契约**。
> 因此本文件定义的是**旁路载荷**，与 `contract.py` 的 9 字段帧**互不影响**。

纪律（与 `contract.py` 完全一致）：
  - 字段名、枚举值**只在本文件出现一次**；Python 侧真实产出与 JS 侧
    （`frontend/mock.js` 的 `validateAgeState`）都必须过本文件的规则；
  - 两边判断不一致 = 集成时必然返工，这是本项目最贵的一类事故（《02》风险表）。

三条不可动摇的设计约束（都来自项目铁律）：
  1. **没有证据的年龄推断不许冒充结论。** 每个推断都带 `engine` 与 `placeholder`；
     `placeholder=True` 表示该引擎**没有任何精度证据**（如零依赖启发式），
     报告/答辩中不得引用其数值。只有 `profile.confirmed=True` 的年龄才允许用来
     选择参照组 —— 推断只提供"建议"，决定权在用户。
  2. **一次正式确认之后，图像推断停止。** `profile.confirmed` 一旦为真且
     `locked=True`，A 线**不得再调用**任何图像年龄引擎（`inference_calls_after_lock`
     必须为 0）。这是用户明确要求的行为，也是隐私最小化的落点。
  3. **不保存人脸原图。** 默认 `privacy.stores_face_image=False`；档案里保存的是
     **不可逆的降采样特征签名**（见 `backend/age_infer.py::FaceSignature`），
     不是照片、不是人脸视频，绝不外传（《05》铁律 4）。
"""

from __future__ import annotations

import json

try:
    from .console import enable_utf8_console
except ImportError:  # 直接以脚本方式运行
    from console import enable_utf8_console

enable_utf8_console()   # 同 contract.py：任何入口的中文输出都不会因代码页崩掉

# 与 backend/contract.py 的 CONTRACT_VERSION 是**两条独立的版本线**：
# 契约帧冻结为 v1.1；年龄旁路是新加的载荷，从 age-v1 起步。
AGE_SCHEMA_VERSION = "age-v1"

# ---------------------------------------------------------------------------
# 1) 年龄参照组（reference bands）
# ---------------------------------------------------------------------------
# 分组依据（谁能改这里）：
#   · 分组边界**不是随便切的**，它必须能对上"公开人群基线研究的分组方式"，
#     否则"与同龄典型数据库比对"就无从谈起。真实来源与核实状态见
#     `data/reference/age_bands.json` 的每条 `source` / `confidence` 字段
#     （该文件里凡未核实的条目一律标 【不确定】，且**不填数值**）。
#   · 改动分组 = 改参照基线 = 必须同时改 `data/reference/age_bands.json`
#     并重跑 `python metrics/scripts/check_age_sidecar.py`。
#
# 为什么留出 child/teen 两档：本设备是"桌面专注"场景（学生用户），
# 儿童与青少年的眨眼率、闭眼时长基线明显不同于成年人，混在一起会让
# "偏离基线"的判定系统性偏错。
AGE_BANDS: tuple[str, ...] = (
    "child",         # 6~12 岁
    "teen",          # 13~17 岁
    "young_adult",   # 18~25 岁
    "adult",         # 26~45 岁
    "middle_aged",   # 46~59 岁
    "older_adult",   # 60 岁及以上
)

# 半开区间 [min, max)，与项目 ROI 的半开口径一致（见 config.yaml roi_is_half_open）。
# None 表示该端不设限。
AGE_BAND_RANGES: dict[str, tuple[int | None, int | None]] = {
    "child": (6, 13),
    "teen": (13, 18),
    "young_adult": (18, 26),
    "adult": (26, 46),
    "middle_aged": (46, 60),
    "older_adult": (60, None),
}

AGE_BAND_ZH: dict[str, str] = {
    "child": "儿童（6~12）",
    "teen": "青少年（13~17）",
    "young_adult": "青年（18~25）",
    "adult": "成年（26~45）",
    "middle_aged": "中年（46~59）",
    "older_adult": "老年（60+）",
}

# 全局兜底组的标识：**没有确认年龄**时用它。
# 刻意不叫 "unknown 组"：它不是一个人群，只是全人群混合基线，
# 因此比对结果里必须如实写 "band_applied=global"，让报告能看出这一差别。
GLOBAL_BAND = "global"

# ---------------------------------------------------------------------------
# 2) 枚举
# ---------------------------------------------------------------------------

# 年龄的来源。注意 `image_estimate` **不能**直接用于选择参照组：
# 它需要先被用户确认（转成 `manual_input` 的语义）才能进入 confirmed 状态。
AGE_SOURCES: tuple[str, ...] = (
    "unset",           # 尚未确定
    "image_estimate",  # 由图像推断（仅建议，未确认）
    "manual_input",    # 用户主动输入并确认
    "enrolled_profile",  # 命中已收录档案（同一张脸的历史确认值）
)

# 年龄推断引擎。新增引擎 = 改本枚举 + 在 age_infer.py 注册 + 补测试。
AGE_ENGINES: tuple[str, ...] = (
    "none",           # 没有跑任何引擎（已锁定，或未启用）
    "manual",         # 用户输入，不涉及图像
    "opencv_caffe",   # OpenCV DNN + Levi & Hassner 8 档年龄模型
    "heuristic",      # 零依赖几何启发式（**占位，无精度证据**）
)

# 收录（enrollment）状态机。
ENROLL_STATES: tuple[str, ...] = (
    "idle",       # 未开始观察（没脸 / 未启用）
    "observing",  # 正在观察窗口内累计样本
    "enrolled",   # 已收录这张脸（之后可识别"同一个人"）
    "rejected",   # 窗口结束但样本不足/不稳定 → 不收录
    "locked",     # 年龄已确认 → 不再做收录判断
)

# 耦合风险档位。`unknown` 是**一等公民**：质量门控不过时必须是 unknown，
# 而不是硬给一个 low（那正是"先判断能不能测再决定测出什么"要禁止的行为）。
RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "unknown")

# Levi & Hassner (2015) 8 档年龄模型的原始标签（OpenCV age_net 用的就是它）。
# 出处：Gil Levi, Tal Hassner, "Age and Gender Classification using Convolutional
#       Neural Networks", CVPR Workshops 2015. 官方权重 deploy_age.prototxt /
#       age_net.caffemodel。
# ⚠️ 这 8 档的**边界与本文件的 AGE_BANDS 不一致**（例如 25~32 跨了
#    young_adult/adult 的 26 岁界）。因此映射按**档中心**归入参照组，
#    并如实记录 `cv_band` 原文，绝不假装两者等价。
CV_AGE_BANDS: tuple[str, ...] = (
    "(0-2)", "(4-6)", "(8-12)", "(15-20)", "(25-32)", "(38-43)", "(48-53)", "(60-100)",
)

# CV 8 档 → 参照组（按档中心）。映射理由逐条写在注释里，便于评审复核。
CV_BAND_TO_AGE_BAND: dict[str, str] = {
    "(0-2)": "child",          # 中心 1 岁 → 低于 child 下限，归入最小组
    "(4-6)": "child",          # 中心 5 岁 → 同上（本设备不面向该年龄段，仅保持映射完整）
    "(8-12)": "child",         # 中心 10 岁 → child
    "(15-20)": "teen",         # 中心 17.5 岁 → teen（13~17）与 young_adult 交界，取 teen
    "(25-32)": "young_adult",  # 中心 28.5 岁 → 跨 26 岁界，取 young_adult（偏保守：不把青年算成中年）
    "(38-43)": "adult",        # 中心 40.5 岁 → adult
    "(48-53)": "middle_aged",  # 中心 50.5 岁 → middle_aged
    "(60-100)": "older_adult",  # 中心 80 岁 → older_adult
}

# 各参照组在 CV 8 档上的"档中心年龄"，用于把 8 档的 softmax 概率折算成
# 一个带不确定度的年龄点估计。**这是算术，不是实验结论**：
# 点估计 = Σ p_i · center_i，区间 = 由累计概率 ≥ 0.6 的档并集给出。
CV_BAND_CENTER_YEARS: dict[str, float] = {
    "(0-2)": 1.0, "(4-6)": 5.0, "(8-12)": 10.0, "(15-20)": 17.5,
    "(25-32)": 28.5, "(38-43)": 40.5, "(48-53)": 50.5, "(60-100)": 80.0,
}

# ---------------------------------------------------------------------------
# 3) 构造与校验
# ---------------------------------------------------------------------------

_PROFILE_KEYS = (
    "subject", "age_years", "band", "source", "confirmed", "confirmed_by",
    "confirmed_at", "locked", "engine", "placeholder", "inference_calls_after_lock",
)

# "谁确认的" —— 只有人能确认，图像推断永远不能自己确认。
#   user_manual            = 用户主动输入年龄后确认
#   user_accepted_estimate = 用户点了"接受这个推断"（数值来自图像，但确认动作是人做的）
CONFIRMED_BY: tuple[str, ...] = ("unset", "user_manual", "user_accepted_estimate")
_ENROLL_KEYS = (
    "state", "window_s", "observed_s", "samples", "needed", "progress",
    "same_face", "distance", "reason",
)
_ESTIMATE_KEYS = (
    "band", "age_years", "age_low", "age_high", "confidence", "engine",
    "placeholder", "cv_band", "cv_probs", "note",
)
_COMPARISON_KEYS = (
    "comparable", "band_applied", "risk", "risk_level", "deviation",
    "quality_gate", "contributions", "interactions", "reason",
    "reference_version", "reference_sources",
)
_PRIVACY_KEYS = ("stores_face_image", "signature_kind", "note")

_TOP_KEYS = ("schema", "ts", "frame_id", "profile", "enrollment", "estimate",
             "comparison", "privacy")


def band_for_age(age_years: float) -> str | None:
    """年龄 → 参照组。落在所有区间之外（如 3 岁）返回 None，绝不硬塞进某一组。

    返回 None 时调用方必须退化为 `GLOBAL_BAND` 并在 reason 里说明 ——
    "这个年龄没有可用的同龄基线"是一个**必须如实输出的结论**。
    """
    if age_years is None:
        return None
    if age_years < 0 or age_years > 120:
        return None
    for band in AGE_BANDS:
        lo, hi = AGE_BAND_RANGES[band]
        if (lo is None or age_years >= lo) and (hi is None or age_years < hi):
            return band
    return None


def empty_profile() -> dict:
    """未确定年龄时的档案骨架（所有入口都用它，避免各处各写一份默认值）。"""
    return {
        "subject": None,
        "age_years": None,
        "band": None,
        "source": "unset",
        "confirmed": False,
        "confirmed_by": "unset",
        "confirmed_at": None,
        "locked": False,
        "engine": "none",
        "placeholder": False,
        "inference_calls_after_lock": 0,
    }


def empty_estimate() -> dict:
    """没有做任何推断时的骨架。`engine=none` + 全 None 表示"本次没有推断"，
    这与"推断失败"是两回事，前端必须能区分（前者不显示推断卡片）。"""
    return {
        "band": None, "age_years": None, "age_low": None, "age_high": None,
        "confidence": None, "engine": "none", "placeholder": False,
        "cv_band": None, "cv_probs": None, "note": None,
    }


def empty_comparison() -> dict:
    """不可比时的骨架。`comparable=False` + `risk=None` 是**正确输出**，
    不是"功能没做好"（同 `vital.*` 为 null 的既有口径）。"""
    return {
        "comparable": False, "band_applied": None, "risk": None,
        "risk_level": "unknown", "deviation": None, "quality_gate": None,
        "contributions": [], "interactions": [], "reason": "尚未开始比对",
        "reference_version": None, "reference_sources": [],
    }


def empty_enrollment(window_s: float = 0.0, needed: int = 0) -> dict:
    return {
        "state": "idle", "window_s": float(window_s), "observed_s": 0.0,
        "samples": 0, "needed": int(needed), "progress": 0.0,
        "same_face": None, "distance": None, "reason": "未开始观察",
    }


def new_age_state(
    *,
    ts: float,
    frame_id: int,
    profile: dict | None = None,
    enrollment: dict | None = None,
    estimate: dict | None = None,
    comparison: dict | None = None,
    stores_face_image: bool = False,
) -> dict:
    """构造一份年龄档案件。键顺序固定，便于 diff 与人工阅读。"""
    return {
        "schema": AGE_SCHEMA_VERSION,
        "ts": round(float(ts), 3),
        "frame_id": int(frame_id),
        "profile": dict(profile) if profile else empty_profile(),
        "enrollment": dict(enrollment) if enrollment else empty_enrollment(),
        "estimate": dict(estimate) if estimate else empty_estimate(),
        "comparison": dict(comparison) if comparison else empty_comparison(),
        "privacy": {
            "stores_face_image": bool(stores_face_image),
            "signature_kind": "gray16x16-int8" if not stores_face_image else "gray16x16-int8+crop",
            "note": ("档案只保存不可逆的降采样签名，不保存人脸原图，也不外传"
                     if not stores_face_image else
                     "已显式开启人脸裁剪保存（本地），存在隐私风险，仅限授权场景"),
        },
    }


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_age_state(obj: dict) -> list[str]:
    """校验一份年龄档案件。返回错误列表，空列表 = 通过。

    刻意与 `contract.validate_frame` 同样的手写风格：报错要能直接指出
    "哪个字段错在哪"，且只依赖标准库（干净机器可复现）。
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return [f"顶层必须是 object，实际是 {type(obj).__name__}"]

    missing = [k for k in _TOP_KEYS if k not in obj]
    if missing:
        errors.append(f"缺少顶层字段：{missing}")
    extra = [k for k in obj if k not in _TOP_KEYS]
    if extra:
        errors.append(f"出现 schema 外顶层字段：{extra}（schema 不改就不许加字段）")

    if obj.get("schema") != AGE_SCHEMA_VERSION:
        errors.append(f"schema 必须是 {AGE_SCHEMA_VERSION!r}，实际 {obj.get('schema')!r}")
    if not _num(obj.get("ts")):
        errors.append(f"ts 必须是数字，实际 {obj.get('ts')!r}")
    if not isinstance(obj.get("frame_id"), int) or isinstance(obj.get("frame_id"), bool):
        errors.append(f"frame_id 必须是整数，实际 {obj.get('frame_id')!r}")

    # ---- profile ----
    p = obj.get("profile")
    if not isinstance(p, dict):
        errors.append("profile 必须是 object")
    else:
        if set(p) != set(_PROFILE_KEYS):
            errors.append(f"profile 字段不符：缺少 {sorted(set(_PROFILE_KEYS) - set(p))}，"
                          f"多出 {sorted(set(p) - set(_PROFILE_KEYS))}")
        else:
            if p["source"] not in AGE_SOURCES:
                errors.append(f"profile.source 必须是 {AGE_SOURCES} 之一，实际 {p['source']!r}")
            if p["engine"] not in AGE_ENGINES:
                errors.append(f"profile.engine 必须是 {AGE_ENGINES} 之一，实际 {p['engine']!r}")
            for k in ("confirmed", "locked", "placeholder"):
                if not isinstance(p[k], bool):
                    errors.append(f"profile.{k} 必须是布尔，实际 {p[k]!r}")
            if not isinstance(p["inference_calls_after_lock"], int) or p["inference_calls_after_lock"] < 0:
                errors.append("profile.inference_calls_after_lock 必须是非负整数")
            # ⚠️ 本文件最重要的一条业务规则，写成断言而不是注释：
            #    年龄一旦正式确认并锁定，就不允许再有任何图像推断调用。
            if p.get("confirmed") and p.get("locked") and p.get("inference_calls_after_lock", 0) > 0:
                errors.append("年龄已确认并锁定，但 inference_calls_after_lock > 0"
                              "（违反「确认后停用年龄推测」的承诺）")
            if p.get("age_years") is not None:
                if not _num(p["age_years"]) or not (0 <= p["age_years"] <= 120):
                    errors.append(f"profile.age_years 必须在 0~120，实际 {p['age_years']!r}")
            if p.get("band") is not None and p["band"] not in AGE_BANDS:
                errors.append(f"profile.band 必须是 {AGE_BANDS} 之一，实际 {p['band']!r}")
            # 已确认就必须有年龄与参照组（否则"确认了什么"无法审计）
            if p.get("confirmed_by") not in CONFIRMED_BY:
                errors.append(f"profile.confirmed_by 必须是 {CONFIRMED_BY} 之一，"
                              f"实际 {p.get('confirmed_by')!r}")
            if p.get("confirmed"):
                if p.get("age_years") is None:
                    errors.append("profile.confirmed=True 时必须有 age_years")
                if p.get("source") == "unset":
                    errors.append("profile.confirmed=True 时 source 不能是 unset")
                if p.get("confirmed_at") is None or not _num(p.get("confirmed_at")):
                    errors.append("profile.confirmed=True 时必须有 confirmed_at 时间戳")
                if p.get("confirmed_by") == "unset":
                    errors.append("profile.confirmed=True 时 confirmed_by 必须是"
                                  " user_manual / user_accepted_estimate"
                                  "（图像推断不得自我确认，必须由人确认）")
            else:
                # 未确认却"锁定"或"标注了确认人"都是自相矛盾的状态。
                if p.get("confirmed_by") != "unset":
                    errors.append("profile.confirmed=False 时 confirmed_by 必须是 unset")
                if p.get("locked"):
                    errors.append("profile.confirmed=False 时 locked 必须为 False"
                                  "（没有确认过的年龄，谈不上锁定）")
            # 接受推断这一动作必须如实标注出来，否则"这个数字是人填的还是模型猜的"
            # 在审计里就分不清了（而两者的可信度完全不同）。
            if p.get("confirmed") and p.get("source") == "image_estimate" \
                    and p.get("confirmed_by") != "user_accepted_estimate":
                errors.append("source=image_estimate 且 confirmed=True 时，"
                              "confirmed_by 必须是 user_accepted_estimate")
            if p.get("subject") is not None and not isinstance(p["subject"], str):
                errors.append("profile.subject 必须是字符串或 null")

    # ---- enrollment ----
    e = obj.get("enrollment")
    if not isinstance(e, dict):
        errors.append("enrollment 必须是 object")
    else:
        if set(e) != set(_ENROLL_KEYS):
            errors.append(f"enrollment 字段不符：缺少 {sorted(set(_ENROLL_KEYS) - set(e))}，"
                          f"多出 {sorted(set(e) - set(_ENROLL_KEYS))}")
        else:
            if e["state"] not in ENROLL_STATES:
                errors.append(f"enrollment.state 必须是 {ENROLL_STATES} 之一，实际 {e['state']!r}")
            for k in ("window_s", "observed_s", "progress"):
                if not _num(e[k]) or e[k] < 0:
                    errors.append(f"enrollment.{k} 必须是非负数字，实际 {e[k]!r}")
            for k in ("samples", "needed"):
                if not isinstance(e[k], int) or isinstance(e[k], bool) or e[k] < 0:
                    errors.append(f"enrollment.{k} 必须是非负整数，实际 {e[k]!r}")
            if not (0.0 <= float(e["progress"]) <= 1.0):
                errors.append(f"enrollment.progress 必须在 0~1，实际 {e['progress']!r}")
            if e["same_face"] is not None and not isinstance(e["same_face"], bool):
                errors.append("enrollment.same_face 必须是布尔或 null")
            if e["distance"] is not None and not _num(e["distance"]):
                errors.append("enrollment.distance 必须是数字或 null")
            if not isinstance(e["reason"], str) or not e["reason"].strip():
                errors.append("enrollment.reason 必须是非空字符串")

    # ---- estimate ----
    es = obj.get("estimate")
    if not isinstance(es, dict):
        errors.append("estimate 必须是 object")
    else:
        if set(es) != set(_ESTIMATE_KEYS):
            errors.append(f"estimate 字段不符：缺少 {sorted(set(_ESTIMATE_KEYS) - set(es))}，"
                          f"多出 {sorted(set(es) - set(_ESTIMATE_KEYS))}")
        else:
            if es["engine"] not in AGE_ENGINES:
                errors.append(f"estimate.engine 必须是 {AGE_ENGINES} 之一，实际 {es['engine']!r}")
            if es["band"] is not None and es["band"] not in AGE_BANDS:
                errors.append(f"estimate.band 必须是 {AGE_BANDS} 之一，实际 {es['band']!r}")
            if es["cv_band"] is not None and es["cv_band"] not in CV_AGE_BANDS:
                errors.append(f"estimate.cv_band 必须是 {CV_AGE_BANDS} 之一，实际 {es['cv_band']!r}")
            for k in ("age_years", "age_low", "age_high"):
                if es[k] is not None and (not _num(es[k]) or not (0 <= es[k] <= 120)):
                    errors.append(f"estimate.{k} 必须是 0~120 的数字或 null，实际 {es[k]!r}")
            if es["confidence"] is not None and (not _num(es["confidence"])
                                                 or not (0.0 <= float(es["confidence"]) <= 1.0)):
                errors.append(f"estimate.confidence 必须在 0~1，实际 {es['confidence']!r}")
            # ⚠️ 没有推断却带数值 = 幻觉数据的温床，直接禁止。
            if es["engine"] == "none" and any(
                es[k] is not None for k in ("band", "age_years", "age_low", "age_high", "confidence")
            ):
                errors.append("estimate.engine=none 时不允许带任何推断数值"
                              "（没有推断就是没有推断）")
            # 占位引擎的自我标注必须是 True —— 防止有人把启发式当结论引用。
            if es["engine"] == "heuristic" and not es["placeholder"]:
                errors.append("estimate.engine=heuristic 必须 placeholder=True"
                              "（该引擎无任何精度证据）")
            if not isinstance(es["placeholder"], bool):
                errors.append("estimate.placeholder 必须是布尔")
            if es["cv_probs"] is not None:
                if (not isinstance(es["cv_probs"], list) or len(es["cv_probs"]) != len(CV_AGE_BANDS)
                        or not all(_num(v) and 0.0 <= float(v) <= 1.0 for v in es["cv_probs"])):
                    errors.append(f"estimate.cv_probs 必须是长度 {len(CV_AGE_BANDS)} 的 0~1 数组或 null")

    # ---- comparison ----
    c = obj.get("comparison")
    if not isinstance(c, dict):
        errors.append("comparison 必须是 object")
    else:
        if set(c) != set(_COMPARISON_KEYS):
            errors.append(f"comparison 字段不符：缺少 {sorted(set(_COMPARISON_KEYS) - set(c))}，"
                          f"多出 {sorted(set(c) - set(_COMPARISON_KEYS))}")
        else:
            if not isinstance(c["comparable"], bool):
                errors.append("comparison.comparable 必须是布尔")
            if c["risk_level"] not in RISK_LEVELS:
                errors.append(f"comparison.risk_level 必须是 {RISK_LEVELS} 之一，实际 {c['risk_level']!r}")
            if c["band_applied"] is not None and c["band_applied"] not in AGE_BANDS + (GLOBAL_BAND,):
                errors.append(f"comparison.band_applied 必须是参照组或 {GLOBAL_BAND}，实际 {c['band_applied']!r}")
            if c["risk"] is not None and (not _num(c["risk"]) or not (0.0 <= float(c["risk"]) <= 1.0)):
                errors.append(f"comparison.risk 必须在 0~1 或 null，实际 {c['risk']!r}")
            # ⚠️ 两条一致性铁律：不可比时不许有数字；可比的 low/medium/high 必须有数字。
            if not c["comparable"]:
                if c["risk"] is not None or c["deviation"] is not None:
                    errors.append("comparable=False 时 risk/deviation 必须是 null"
                                  "（不报跳变数字是本产品的承诺）")
                if c["risk_level"] != "unknown":
                    errors.append("comparable=False 时 risk_level 必须是 unknown")
            else:
                if c["risk"] is None:
                    errors.append("comparable=True 时 risk 不许为 null")
                if c["risk_level"] == "unknown":
                    errors.append("comparable=True 时 risk_level 不能是 unknown")
                if c["band_applied"] is None:
                    errors.append("comparable=True 时必须写明 band_applied（参照组或 global）")
            if not isinstance(c["contributions"], list):
                errors.append("comparison.contributions 必须是数组")
            else:
                for i, item in enumerate(c["contributions"]):
                    if not isinstance(item, dict) or "metric" not in item or "log_odds" not in item:
                        errors.append(f"comparison.contributions[{i}] 至少要有 metric 与 log_odds")
            if not isinstance(c["interactions"], list):
                errors.append("comparison.interactions 必须是数组")
            if not isinstance(c["reason"], str) or not c["reason"].strip():
                errors.append("comparison.reason 必须是非空字符串")
            if not isinstance(c["reference_sources"], list):
                errors.append("comparison.reference_sources 必须是数组")

    # ---- privacy ----
    pv = obj.get("privacy")
    if not isinstance(pv, dict):
        errors.append("privacy 必须是 object")
    else:
        if set(pv) != set(_PRIVACY_KEYS):
            errors.append(f"privacy 字段不符：缺少 {sorted(set(_PRIVACY_KEYS) - set(pv))}，"
                          f"多出 {sorted(set(pv) - set(_PRIVACY_KEYS))}")
        else:
            if not isinstance(pv["stores_face_image"], bool):
                errors.append("privacy.stores_face_image 必须是布尔")
            if not isinstance(pv["note"], str) or not pv["note"].strip():
                errors.append("privacy.note 必须是非空字符串")

    return errors


def dumps(obj: dict) -> str:
    """单行 JSON（与 contract.dumps 同口径：保留中文、紧凑分隔符）。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def assert_valid(obj: dict) -> dict:
    errs = validate_age_state(obj)
    if errs:
        raise ValueError("年龄档案件校验失败：\n  - " + "\n  - ".join(errs))
    return obj


if __name__ == "__main__":  # 自检：python backend/age_contract.py
    ok = new_age_state(ts=1.5, frame_id=3)
    print("空档案件校验：", "通过" if not validate_age_state(ok) else validate_age_state(ok))
    probe = new_age_state(ts=1.5, frame_id=3)
    probe["profile"].update({"age_years": 30, "band": band_for_age(30), "confirmed": True,
                             "confirmed_at": 1.5, "source": "manual_input",
                             "locked": True, "inference_calls_after_lock": 1})
    print("锁定后仍调推断 →", validate_age_state(probe)[:1])
    print("30 岁 → 参照组", band_for_age(30), "| 17 岁 →", band_for_age(17),
          "| 3 岁 →", band_for_age(3))
    print("CV 档映射 (25-32) →", CV_BAND_TO_AGE_BAND["(25-32)"])
