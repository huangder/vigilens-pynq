"""age_infer.py —— PS 端"由图像推测年龄"的**可插拔引擎** + 人脸收录状态机 + 记录功能。

三条线路的 A 线路部分（软件路径）。本文件负责：

  1. **年龄推断引擎**（`AgeEngine`）：`opencv_caffe`（真实模型）/ `heuristic`（零依赖占位）。
     引擎可插拔、按 `config.yaml` 的 `age_engine_order` 选优；全都不可用时返回 `None`，
     契约层写 `engine=none` —— **不猜**。
  2. **人脸签名**（`FaceSignature`）：把 bbox 裁剪降采样成 16×16 灰度、去均值、量化成
     256 字节 int8。用途是"判断是不是同一个人"，**不是**保存人脸照片。
  3. **收录状态机**（`AgeProfileStore`）：用户要求的"因为识别出来了人脸，给予年龄识别机制
     一个判断是否收录人脸的时间"就落在这里 —— 在一个可配置的观察窗口（默认 20 s）内
     累计**有效**样本，窗口结束（或用户直接表态）才决定收录 / 不收录。
  4. **一次性确认后停用推断**：`profile.confirmed && locked` 之后
     `AgeProfileStore.inference_allowed()` 恒为 False；若还有调用方硬调
     `maybe_infer()`，会把 `inference_calls_after_lock` 加一 —— 于是
     `backend/age_contract.py::validate_age_state` 会**当场报错**，违规留痕而不是静默发生。
  5. **记录功能**：每次推断、收录判断、人工确认、重置都写一条事件（内存 + 可选落库）。

⚠️ 诚实边界（写代码的人必须自己守住）：
  · `heuristic` 引擎的输出**没有任何精度证据**（placeholder=True）。它是为了让链路、
    状态机、界面在没有模型权重时也能被测试与演示，**不得**在报告/答辩里当结论引用。
  · `opencv_caffe` 用的是 Levi & Hassner (2015) 8 档分类模型，其公开数据集上的指标
    **不等于**本项目场景的精度（本项目的相机、光照、角度都不同）。在真机标定之前，
    它的输出同样**只是建议**，必须经用户确认才能进入参照组选择。
  · 本文件**不做**疾病/健康诊断，只服务于"疲劳趋势监测 / 辅助提示"的定位（《05》铁律 5）。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from .age_contract import (AGE_BANDS, CV_AGE_BANDS, CV_BAND_CENTER_YEARS,
                               CV_BAND_TO_AGE_BAND, GLOBAL_BAND, band_for_age,
                               empty_enrollment, empty_estimate, empty_profile,
                               new_age_state)
    from .config import REPO_ROOT, load_config
except ImportError:  # 直接以脚本方式运行
    from age_contract import (AGE_BANDS, CV_AGE_BANDS, CV_BAND_CENTER_YEARS,
                              CV_BAND_TO_AGE_BAND, GLOBAL_BAND, band_for_age,
                              empty_enrollment, empty_estimate, empty_profile,
                              new_age_state)
    from config import REPO_ROOT, load_config

# 事件类型（写进 DB 的 age_events.kind；前端也会按它分组显示）
EVENT_KINDS = (
    "enroll_observe_start", "enroll_observe_restart", "enroll_decided",
    "estimate_proposed", "estimate_rejected", "manual_confirmed",
    "estimate_accepted", "profile_reset", "inference_refused_locked",
)

# 各档在"以档中心折算点估计"时的权重上限：占位引擎的置信度被硬压在这个值以下，
# 免得它看起来像一次可信的推断（这不是标定，是**诚实性上限**）。
PLACEHOLDER_CONFIDENCE_CAP = 0.25


@dataclass
class AgeEstimate:
    """一次图像年龄推断的结果。字段与 age_contract 的 `estimate` 段一一对应。"""

    engine: str
    placeholder: bool
    band: str | None = None
    age_years: float | None = None
    age_low: float | None = None
    age_high: float | None = None
    confidence: float | None = None
    cv_band: str | None = None
    cv_probs: list[float] | None = None
    note: str | None = None

    def to_estimate_block(self) -> dict:
        """转成契约里的 `estimate` 段（字段恰好为 schema 声明的那几个）。"""
        return {
            "band": self.band, "age_years": self.age_years,
            "age_low": self.age_low, "age_high": self.age_high,
            "confidence": self.confidence, "engine": self.engine,
            "placeholder": bool(self.placeholder), "cv_band": self.cv_band,
            "cv_probs": self.cv_probs, "note": self.note,
        }


# ---------------------------------------------------------------------------
# 1) 人脸签名（不是照片）
# ---------------------------------------------------------------------------

class FaceSignature:
    """人脸的低维、量化签名：16×16 灰度 → 去均值 → 缩放 → int8（256 字节）。

    隐私设计（为什么这样做，而不是存裁剪图）：
      · 已授权场景也不该在本地堆一摞人脸照片 —— 那是**可辨识的个人生物特征**；
      · 本签名只有 256 字节，去掉了绝对亮度（去均值）并量化到 int8，
        **不能还原成一张能被认出来的脸**；它的唯一用途是"这次的脸和上次是不是同一个人"；
      · 它仍属于**由人脸派生的个人信息**，因此：默认只留本地、不入库导出、不外传，
        并且 `privacy.stores_face_image=false` 时要如实说明"签名不是照片"。

    ⚠️ 边界：本类是"足够好用的近似"，不是生物特征识别级别的方案（没有活体检测、
      对姿态/光照敏感）。因此**任何身份判断都只用于提示**，绝不用于安全或门禁语义。
    """

    @staticmethod
    def from_image(image: Any, bbox: Any, dim: int = 16) -> bytes | None:
        """返回 dim*dim 字节的 int8 签名；图像不可用 / bbox 无效时返回 None。"""
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            return None
        if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
            return None
        h, w = image.shape[0], image.shape[1]
        try:
            x, y, bw, bh = (int(v) for v in bbox)
        except (TypeError, ValueError):
            return None
        # 与 ROI 同一套半开口径：先夹到图像内，再拒绝退化框。
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        crop = image[y0:y1, x0:x1]
        if crop.ndim == 3:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(crop, (dim, dim), interpolation=cv2.INTER_AREA).astype(np.float32)
        small -= float(small.mean())
        std = float(small.std())
        if std < 1e-6:
            # 完全均匀的裁剪（例如过曝/纯色）没有区分度：**拒绝**而不是返回一个常数签名，
            # 否则所有"平脸"会长得一模一样，制造出假的"同一个人"。
            return None
        small /= std
        q = np.clip(np.round(small * 32.0), -127, 127).astype(np.int8)
        return q.tobytes()

    @staticmethod
    def distance(a: bytes | None, b: bytes | None) -> float | None:
        """两个签名的距离（0 = 一样）。任一为空时返回 None（不可比较）。"""
        if not a or not b or len(a) != len(b):
            return None
        try:
            import numpy as np  # type: ignore
        except ImportError:
            return None
        qa = np.frombuffer(a, dtype=np.int8).astype(np.float32)
        qb = np.frombuffer(b, dtype=np.int8).astype(np.float32)
        return float(np.sqrt(np.mean((qa - qb) ** 2)) / 127.0)

    @staticmethod
    def subject_id(sig: bytes) -> str:
        """由签名派生的匿名主体标识：`sub-<12 hex>`。

        用哈希而不是自增序号，是为了**不把"第几个用户"这种可关联信息**写进库；
        同时同一个人在同一台设备上总会得到同一个 id（签名确定性）。
        """
        return "sub-" + hashlib.sha1(sig).hexdigest()[:12]


# ---------------------------------------------------------------------------
# 2) 年龄推断引擎
# ---------------------------------------------------------------------------

class AgeEngine:
    """引擎接口。子类必须给出 `name` / `placeholder` / `available()` / `estimate()`。"""

    name: str = "none"
    placeholder: bool = False

    def available(self) -> bool:  # pragma: no cover - 由子类实现
        raise NotImplementedError

    def estimate(self, image: Any, bbox: Any) -> AgeEstimate | None:  # pragma: no cover
        raise NotImplementedError


class OpenCvCaffeAgeEngine(AgeEngine):
    """OpenCV DNN + Levi & Hassner 8 档年龄分类模型（真实引擎，需权重文件）。

    出处（供评审核对，**不要把公开数据集指标当成本项目精度**）：
      · Gil Levi, Tal Hassner, "Age and Gender Classification using Convolutional
        Neural Networks", CVPR Workshops 2015 —— 8 档年龄分类的原始论文；
      · OpenCV 官方示例 `samples/dnn/age_gender.py` 用的就是
        `deploy_age.prototxt` + `age_net.caffemodel`，输入 227×227、均值
        (78.4263377603, 87.7689143744, 114.895847746)（即 8 档分类网络的官方口径）。

    ⚠️ 未验证项（**不得**在报告里当成已完成的成果）：
      · 未在 Mizar-Z7020 的 Cortex-A9（PS 侧）上测过单帧耗时；
      · 未在本项目的相机/光照/距离下做过精度评估；
      · 权重文件不入库（见 .gitignore），缺文件时本引擎 `available()` 为 False。
    """

    name = "opencv_caffe"
    placeholder = False

    def __init__(self, model_dir: str | Path) -> None:
        self.model_dir = Path(model_dir)
        self.prototxt = self.model_dir / "deploy_age.prototxt"
        self.weights = self.model_dir / "age_net.caffemodel"
        self._net = None
        self._load_error: str | None = None

    def available(self) -> bool:
        try:
            import cv2  # type: ignore  # noqa: F401
        except ImportError:
            self._load_error = "未安装 opencv-python"
            return False
        if not self.prototxt.exists():
            self._load_error = f"缺少 {self.prototxt}"
            return False
        if not self.weights.exists():
            self._load_error = f"缺少 {self.weights}（用 metrics/scripts/fetch_age_model.py 取）"
            return False
        return True

    def _ensure_net(self) -> Any:
        if self._net is not None:
            return self._net
        import cv2  # type: ignore

        # 只读加载；模型文件来自官方发布，路径由 config 给出（不联网下载）。
        self._net = cv2.dnn.readNetFromCaffe(str(self.prototxt), str(self.weights))
        return self._net

    def estimate(self, image: Any, bbox: Any) -> AgeEstimate | None:
        if not self.available():
            return None
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            return None
        if not isinstance(image, np.ndarray) or image.ndim != 3:
            return None
        h, w = image.shape[0], image.shape[1]
        x, y, bw, bh = (int(v) for v in bbox)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        if x1 - x0 < 16 or y1 - y0 < 16:
            return None
        crop = image[y0:y1, x0:x1]
        try:
            net = self._ensure_net()
            blob = cv2.dnn.blobFromImage(
                crop, 1.0, (227, 227),
                (78.4263377603, 87.7689143744, 114.895847746),
                swapRB=False, crop=False,
            )
            net.setInput(blob)
            probs = net.forward()[0]
        except Exception as exc:  # noqa: BLE001 —— 推断失败不能让整条测量链断掉
            self._load_error = f"前向推理失败：{exc}"
            return None

        probs = [float(p) for p in probs]
        top = int(max(range(len(probs)), key=lambda i: probs[i]))
        cv_band = CV_AGE_BANDS[top]
        # 点估计 = 各档概率 × 档中心。这是**算术折算**，不是模型给出的回归值，
        # 因此区间也一并给出，避免下游把点估计当成精确年龄。
        point = sum(p * CV_BAND_CENTER_YEARS[b] for p, b in zip(probs, CV_AGE_BANDS))
        order = sorted(range(len(probs)), key=lambda i: -probs[i])
        acc, low_i, high_i = 0.0, order[0], order[0]
        for i in order:
            acc += probs[i]
            low_i, high_i = min(low_i, i), max(high_i, i)
            if acc >= 0.60:
                break
        age_low = CV_BAND_CENTER_YEARS[CV_AGE_BANDS[low_i]]
        age_high = CV_BAND_CENTER_YEARS[CV_AGE_BANDS[high_i]]
        return AgeEstimate(
            engine=self.name, placeholder=False,
            band=CV_BAND_TO_AGE_BAND[cv_band], age_years=round(point, 1),
            age_low=age_low, age_high=age_high,
            confidence=round(probs[top], 4), cv_band=cv_band,
            cv_probs=[round(p, 6) for p in probs],
            note=("Levi & Hassner (2015) 8 档分类模型；公开数据集指标不等于本项目精度，"
                  "A9 侧耗时未测。输出仅作建议，需用户确认。"),
        )


class HeuristicAgeEngine(AgeEngine):
    """零依赖占位引擎：**没有任何精度证据**，只为让链路/状态机/界面可测可演示。

    为什么还要写它：项目纪律是"没有权重也要能端到端跑通"。缺了它，
    没下模型权重的人就无法验证收录状态机、旁路 schema 与前端面板。
    它的输出被判为 `placeholder=True`，且置信度被硬压在
    `PLACEHOLDER_CONFIDENCE_CAP` 以下，前端会显示"不得作为结论"。

    两个粗糙代理（都**不是**已验证的年龄特征，仅用于产生确定性差异）：
      · 面部纹理高频能量：随年龄皮肤纹理变化（文献有讨论，但本实现**未做任何验证**）；
      · 人脸框纵横比：粗略反映面部长宽比变化。
    输出刻意给**宽区间**（相邻两档合并），让"不确定性大"这件事写在数据里。
    """

    name = "heuristic"
    placeholder = True

    def available(self) -> bool:
        try:
            import cv2  # type: ignore  # noqa: F401
            import numpy  # type: ignore  # noqa: F401
        except ImportError:
            return False
        return True

    def estimate(self, image: Any, bbox: Any) -> AgeEstimate | None:
        if not self.available():
            return None
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            return None
        if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
            return None
        h, w = image.shape[0], image.shape[1]
        x, y, bw, bh = (int(v) for v in bbox)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + bw), min(h, y + bh)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return None
        crop = image[y0:y1, x0:x1]
        if crop.ndim == 3:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA).astype(np.float32)
        # 纹理能量：与 3x3 均值模糊之差的标准差（越大 = 纹理越"粗"）。
        blur = cv2.blur(small, (3, 3))
        texture = float((small - blur).std())
        aspect = float(max(x1 - x0, 1)) / float(max(y1 - y0, 1))
        # 把两个代理拼成一个 0~1 的"伪年龄分位"（线性映射，纯算术，无实验依据）。
        score = 0.5 * min(1.0, max(0.0, texture / 12.0)) + 0.5 * min(1.0, max(0.0, (0.85 - aspect) / 0.35))
        idx = min(len(AGE_BANDS) - 1, int(score * len(AGE_BANDS)))
        band = AGE_BANDS[idx]
        lo, hi = _band_bounds(band)
        center = (lo + (hi if hi is not None else lo + 12)) / 2.0
        return AgeEstimate(
            engine=self.name, placeholder=True, band=band,
            age_years=round(center, 1), age_low=float(lo), age_high=None if hi is None else float(hi),
            confidence=round(min(PLACEHOLDER_CONFIDENCE_CAP, 0.10 + 0.15 * score), 4),
            cv_band=None, cv_probs=None,
            note=("占位启发式（面部纹理 + 人脸框纵横比），**无任何精度证据**，"
                  "仅供链路演示与状态机测试，不得作为结论引用。"),
        )


def _band_bounds(band: str) -> tuple[int, int | None]:
    try:
        from .age_contract import AGE_BAND_RANGES
    except ImportError:
        from age_contract import AGE_BAND_RANGES
    lo, hi = AGE_BAND_RANGES[band]
    return (0 if lo is None else lo, hi)


def make_age_engine(cfg: dict[str, Any]) -> AgeEngine | None:
    """按 `age_engine_order` 返回第一个可用引擎；全不可用时返回 None（= engine:none）。"""
    order = cfg.get("age_engine_order") or ["opencv_caffe", "heuristic"]
    model_dir = REPO_ROOT / str(cfg.get("age_model_dir") or "data/models/age")
    for name in order:
        engine: AgeEngine | None = None
        if name == "opencv_caffe":
            engine = OpenCvCaffeAgeEngine(model_dir)
        elif name == "heuristic":
            engine = HeuristicAgeEngine()
        if engine is not None and engine.available():
            return engine
    return None


# ---------------------------------------------------------------------------
# 3) 年龄档案 + 收录状态机 + 记录功能
# ---------------------------------------------------------------------------

class AgeProfileStore:
    """一次运行对应一个实例：持有档案、观察窗口、推断节流与事件记录。

    与 DB / 前端的耦合都通过回调与 `sidecar()` 输出，本类**不**自己写文件，
    这样它可以在没有 sqlite、没有 FastAPI 的干净机器上被单测直接驱动。
    """

    def __init__(self, cfg: dict[str, Any] | None = None, *,
                 on_event: Callable[[dict], None] | None = None,
                 on_subject: Callable[[dict], None] | None = None,
                 now: Callable[[], float] = time.time) -> None:
        c = cfg or load_config()
        self.cfg = c
        self.on_event = on_event
        self.on_subject = on_subject
        self._now = now

        self.enabled = bool(c.get("age_enabled", True))
        self.infer_hz = float(c.get("age_infer_hz", 1.0) or 0.0)
        self.enroll_window_s = float(c.get("age_enroll_window_s", 20.0))
        self.enroll_min_samples = int(c.get("age_enroll_min_samples", 12))
        self.enroll_min_quality = float(c.get("age_enroll_min_quality", 0.65))
        self.signature_dim = int(c.get("age_signature_dim", 16))
        self.match_max_distance = float(c.get("age_signature_match_max_distance", 0.35))
        self.restart_frames = int(c.get("age_enroll_restart_frames", 3))
        self.stores_face_image = bool(c.get("age_stores_face_image", False))
        self.confirm_locks = bool(c.get("age_confirm_locks_inference", True))

        self.profile: dict[str, Any] = empty_profile()
        self.enrollment: dict[str, Any] = empty_enrollment(self.enroll_window_s,
                                                           self.enroll_min_samples)
        self.estimate: AgeEstimate | None = None
        self.estimate_ts: float | None = None
        self.events: list[dict[str, Any]] = []

        # ---- 引擎与节流 ----
        self.engine: AgeEngine | None = make_age_engine(c) if self.enabled else None
        self._last_infer_logical: float | None = None
        self.inference_calls = 0
        self.inference_refused_locked = 0

        # ---- 观察窗口内部状态 ----
        self._ref_signature: bytes | None = None
        self._sig_samples: list[bytes] = []
        self._observe_start_ts: float | None = None
        self._mismatch_streak = 0
        self._enrolled_signature: bytes | None = None
        self._estimate_suppressed = False

    # -- 记录功能 --------------------------------------------------------
    def log(self, kind: str, *, ts: float | None = None, **detail: Any) -> dict:
        """写一条年龄档案事件。内存留一份，并通过回调（DB）落库。"""
        ev = {"at": round(float(self._now() if ts is None else ts), 3),
              "kind": kind, "detail": detail}
        self.events.append(ev)
        if self.on_event is not None:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001 —— 落库失败不许打断测量
                pass
        return ev

    def drain_events(self) -> list[dict]:
        """取出并清空本次新增事件（run_pipeline 的 summary 用）。"""
        out, self.events = self.events, []
        return out

    # -- 推断门控 --------------------------------------------------------
    def locked(self) -> bool:
        return bool(self.profile["confirmed"] and self.profile["locked"])

    def inference_allowed(self) -> bool:
        """**用户要求的那条规则**：一次正式确认年龄后，不再采用图像推测机制。"""
        if not self.enabled or self.engine is None:
            return False
        if self.confirm_locks and self.locked():
            return False
        return True

    def should_infer_logical(self, logical_t: float) -> bool:
        """按**逻辑时间**（frame_id/fps，可复现）判定本次是否该跑推断。

        用逻辑时间而不是墙上时钟：同一段回放跑两次必须得到同一串推断调用，
        否则"同视频重复运行结果一致"的验收口径会被打破（见 run_pipeline 文件头第 1 条）。
        """
        if not self.inference_allowed():
            return False
        period = 1.0 / self.infer_hz if self.infer_hz > 0 else float("inf")
        if self._last_infer_logical is None or (logical_t - self._last_infer_logical) >= period - 1e-9:
            self._last_infer_logical = logical_t
            return True
        return False

    def maybe_infer(self, *, frame_id: int, logical_t: float, image: Any, bbox: Any,
                    ts: float | None = None) -> AgeEstimate | None:
        """跑一次推断并记录。**被锁定后若还有人调用，会留痕**（不静默）。"""
        if not self.inference_allowed():
            if self.locked():
                self.profile["inference_calls_after_lock"] += 1
                self.inference_refused_locked += 1
                self.log("inference_refused_locked", ts=ts, frame_id=frame_id)
            return None
        if not self.should_infer_logical(logical_t):
            return None
        if self._estimate_suppressed and self.estimate is not None:
            return None
        est = self.engine.estimate(image, bbox) if self.engine is not None else None
        if est is None:
            return None
        self.inference_calls += 1
        self.estimate = est
        self.estimate_ts = float(self._now() if ts is None else ts)
        self.log("estimate_proposed", ts=ts, frame_id=frame_id, engine=est.engine,
                 placeholder=est.placeholder, band=est.band,
                 age_years=est.age_years, confidence=est.confidence)
        return est

    # -- 收录状态机 ------------------------------------------------------
    def observe(self, *, frame_id: int, ts: float, image: Any, bbox: Any,
                quality_overall: float, face_visible: float) -> dict:
        """喂一帧观测，推进"判断是否收录人脸"的窗口。返回 enrollment 段。

        语义（刻意写清楚，因为这是用户点名的功能）：
          · 只有 `quality_overall ≥ age_enroll_min_quality` 且人脸可见的帧才算**有效样本**；
          · 窗口按**第一帧有效样本的墙钟时间**起算，满 `age_enroll_window_s` 后自动判定；
          · 期间若出现"与参考签名不一致"的连续 `age_enroll_restart_frames` 帧，
            说明换人了 → **重启观察**（记一条 enroll_observe_restart 事件）；
          · 判定：有效样本 ≥ `age_enroll_min_samples` → 收录（生成匿名 subject id 并回调落库）；
            否则 → 不收录（reason 说明差在哪）。**不收录不是错误**，是如实结论。
        """
        sig = FaceSignature.from_image(image, bbox, self.signature_dim)

        if self.locked():
            # 已确认年龄 → 收录判断本身也停用（不再观察新的人脸）。
            self.enrollment.update({"state": "locked", "reason": "年龄已确认，已停止收录判断"})
            return self.enrollment

        if sig is None or quality_overall < self.enroll_min_quality or face_visible <= 0.0:
            # 本帧不可用：**不推进窗口、不加样本**（但也不清空已累计的观察）。
            self.enrollment["same_face"] = None if sig is None else self.enrollment["same_face"]
            if self.enrollment["state"] == "observing":
                self.enrollment["reason"] = (
                    f"本帧不计入观察（{'没有人脸裁剪' if sig is None else '质量/可见率不足'}："
                    f"质量 {quality_overall:.2f} < {self.enroll_min_quality:.2f}）")
            return self.enrollment

        if self.profile["subject"] is not None and self._enrolled_signature is not None:
            # 已收录：只做"是不是同一个人"的核对，不再改档案。
            dist = FaceSignature.distance(self._enrolled_signature, sig)
            same = dist is not None and dist <= self.match_max_distance
            self.enrollment.update({
                "state": "enrolled", "same_face": bool(same), "distance": dist,
                "reason": ("与已收录档案一致" if same else
                           "当前人脸与已收录档案不一致：为保护既有档案，不覆盖、也不新建"),
            })
            return self.enrollment

        # ---- 观察中 ----
        if self.enrollment["state"] not in ("observing", "rejected", "idle"):
            self.enrollment["state"] = "observing"
        if self._ref_signature is None:
            self._start_observation(sig, frame_id=frame_id, ts=ts)
        else:
            dist = FaceSignature.distance(self._ref_signature, sig)
            if dist is not None and dist > self.match_max_distance:
                self._mismatch_streak += 1
                if self._mismatch_streak >= self.restart_frames:
                    self.log("enroll_observe_restart", ts=ts, frame_id=frame_id,
                             distance=dist, streak=self._mismatch_streak)
                    self._start_observation(sig, frame_id=frame_id, ts=ts)
                self.enrollment["same_face"] = False
                self.enrollment["distance"] = dist
                return self.enrollment
            self._mismatch_streak = 0
            self.enrollment["same_face"] = True
            self.enrollment["distance"] = dist

        self._sig_samples.append(sig)
        self.enrollment["samples"] = len(self._sig_samples)
        # ⚠️ 必须写成显式 None 判断：`self._observe_start_ts or ts` 会把**合法的 0.0**
        #    当成"未设置"，于是每帧都算成"刚观察了 0 秒"，窗口永远不到期
        #    （本文件首版就踩了这个坑，自检里表现为"25 个样本、观察 0.0/20.0 s"）。
        start = self._observe_start_ts
        observed = 0.0 if start is None else max(0.0, ts - start)
        self.enrollment["observed_s"] = round(observed, 3)
        self.enrollment["progress"] = round(
            min(1.0, max(observed / self.enroll_window_s if self.enroll_window_s > 0 else 1.0,
                          len(self._sig_samples) / max(1, self.enroll_min_samples))), 4)
        self.enrollment["state"] = "observing"
        self.enrollment["reason"] = (
            f"正在判断是否收录人脸：{observed:.1f}/{self.enroll_window_s:.1f} s，"
            f"有效样本 {len(self._sig_samples)}/{self.enroll_min_samples}")

        if observed >= self.enroll_window_s:
            self._decide_enrollment(ts=ts, automatic=True)
        return self.enrollment

    def _start_observation(self, sig: bytes, *, frame_id: int, ts: float) -> None:
        self._ref_signature = sig
        self._sig_samples = []
        self._observe_start_ts = ts
        self._mismatch_streak = 0
        self.enrollment.update({
            "state": "observing", "observed_s": 0.0, "samples": 0,
            "progress": 0.0, "same_face": True, "distance": 0.0,
            "reason": "开始观察（等待累计有效样本）",
        })
        self.log("enroll_observe_start", ts=ts, frame_id=frame_id,
                 subject_guess=FaceSignature.subject_id(sig),
                 window_s=self.enroll_window_s, needed=self.enroll_min_samples)

    def _decide_enrollment(self, *, ts: float, automatic: bool,
                           accept: bool | None = None) -> dict:
        enough = len(self._sig_samples) >= self.enroll_min_samples
        take = enough if accept is None else bool(accept)
        if take and self._ref_signature is not None:
            # 用观察窗口内**所有样本的中位数签名**作为档案，比第一帧更稳（对抖动更鲁棒）。
            sig = _median_signature(self._sig_samples) or self._ref_signature
            subject = FaceSignature.subject_id(sig)
            self._enrolled_signature = sig
            self.profile["subject"] = subject
            self.enrollment.update({
                "state": "enrolled", "same_face": True,
                "distance": FaceSignature.distance(sig, self._ref_signature),
                "progress": 1.0,
                "reason": (f"已收录（{'窗口到期自动判定' if automatic else '用户确认'}，"
                           f"有效样本 {len(self._sig_samples)}）"),
            })
            self.log("enroll_decided", ts=ts, accept=True, automatic=automatic,
                     subject=subject, samples=len(self._sig_samples),
                     signature_bytes=len(sig))
            if self.on_subject is not None:
                try:
                    self.on_subject({"subject": subject, "signature": sig,
                                     "samples": len(self._sig_samples), "at": ts})
                except Exception:  # noqa: BLE001
                    pass
        else:
            self.enrollment.update({
                "state": "rejected", "progress": 0.0,
                "reason": (f"不收录（{'用户选择不收录' if accept is False else ''}"
                           f"有效样本 {len(self._sig_samples)}/{self.enroll_min_samples}，"
                           f"观察 {self.enrollment['observed_s']:.1f}/{self.enroll_window_s:.1f} s）"),
            })
            self.log("enroll_decided", ts=ts, accept=False, automatic=automatic,
                     samples=len(self._sig_samples), needed=self.enroll_min_samples)
        return self.enrollment

    # -- 用户动作 --------------------------------------------------------
    def set_manual(self, age_years: int, *, ts: float | None = None) -> dict:
        """用户**主动输入**年龄并确认 → 锁定年龄推测（用户明确要求的行为）。"""
        t = float(self._now() if ts is None else ts)
        age = int(age_years)
        band = band_for_age(age)
        self.profile.update({
            "age_years": age, "band": band, "source": "manual_input",
            "confirmed": True, "confirmed_by": "user_manual", "confirmed_at": t,
            "locked": bool(self.confirm_locks), "engine": "manual", "placeholder": False,
        })
        self._estimate_suppressed = True
        self.enrollment.update({"state": "locked", "reason": "年龄已确认，已停止收录判断"})
        self.log("manual_confirmed", ts=t, age_years=age, band=band,
                 locked=self.profile["locked"])
        return self.profile

    def accept_estimate(self, *, ts: float | None = None) -> dict:
        """用户点了"接受这个推断"：数值来自图像，但**确认动作是人做的**（留痕可审计）。"""
        t = float(self._now() if ts is None else ts)
        if self.estimate is None or self.estimate.band is None:
            raise ValueError("当前没有可接受的年龄推断（estimate.band 为空）")
        age = int(round(float(self.estimate.age_years or 0)))
        if not (0 <= age <= 120):
            raise ValueError(f"推断出的年龄不合理：{self.estimate.age_years!r}")
        self.profile.update({
            "age_years": age, "band": band_for_age(age), "source": "image_estimate",
            "confirmed": True, "confirmed_by": "user_accepted_estimate", "confirmed_at": t,
            "locked": bool(self.confirm_locks),
            "engine": self.estimate.engine, "placeholder": bool(self.estimate.placeholder),
        })
        self._estimate_suppressed = True
        self.enrollment.update({"state": "locked", "reason": "年龄已确认，已停止收录判断"})
        self.log("estimate_accepted", ts=t, age_years=age, band=self.profile["band"],
                 engine=self.estimate.engine, placeholder=self.estimate.placeholder)
        return self.profile

    def reject_estimate(self, *, ts: float | None = None) -> dict:
        """忽略推断：本段运行不再重复提议（否则 1 Hz 会一直弹同一个建议）。"""
        self._estimate_suppressed = True
        self.log("estimate_rejected", ts=ts,
                 engine=None if self.estimate is None else self.estimate.engine)
        return self.estimate.to_estimate_block() if self.estimate else empty_estimate()

    def enroll_decision(self, accept: bool, *, ts: float | None = None) -> dict:
        """用户直接表态收录 / 不收录（跳过等待窗口）。"""
        return self._decide_enrollment(ts=float(self._now() if ts is None else ts),
                                       automatic=False, accept=accept)

    def reset(self, *, ts: float | None = None) -> dict:
        """重置档案（解锁 + 清空确认）。**必须显式调用**才能重新启用图像推测。"""
        t = float(self._now() if ts is None else ts)
        self.profile = empty_profile()
        self.enrollment = empty_enrollment(self.enroll_window_s, self.enroll_min_samples)
        self.estimate = None
        self.estimate_ts = None
        self._estimate_suppressed = False
        self._ref_signature = None
        self._sig_samples = []
        self._observe_start_ts = None
        self._mismatch_streak = 0
        self._enrolled_signature = None
        self._last_infer_logical = None
        self.log("profile_reset", ts=t)
        return self.profile

    # -- 输出 ------------------------------------------------------------
    def apply_remote_state(self, sidecar: dict) -> dict:
        """把**另一进程**（A 线 run_pipeline）观测到的状态吸收进本地 store。

        为什么需要：M2 里 A 线（测量进程）与 B 线（服务进程）是两个进程。B 线要能让
        用户在网页上点"接受推断"，就必须先知道**推断是什么** —— 那是 A 线算出来的。
        因此 B 线把 A 线推来的档案件里的 `estimate` / `enrollment` 吸收进来，再对外交互。

        **刻意只吸收"观测"，绝不吸收"决定"**：
          · `estimate` / `enrollment` 是 A 线的观测 → 吸收（否则 UI 无内容可点）；
          · `profile`（年龄、确认人、锁定）是用户的决定 → **永不**被远端覆盖，
            否则任何一个连上来的测量进程都能把用户已确认的年龄改掉，
            而"一次正式确认后不再采用年龄推测"这条承诺会当场失效。
        """
        est = sidecar.get("estimate") or {}
        if est.get("engine") and est.get("engine") != "none":
            self.estimate = AgeEstimate(
                engine=str(est["engine"]), placeholder=bool(est.get("placeholder")),
                band=est.get("band"), age_years=est.get("age_years"),
                age_low=est.get("age_low"), age_high=est.get("age_high"),
                confidence=est.get("confidence"), cv_band=est.get("cv_band"),
                cv_probs=est.get("cv_probs"), note=est.get("note"))
        enr = sidecar.get("enrollment")
        if isinstance(enr, dict) and not self.locked():
            merged = dict(self.enrollment)
            for k in ("state", "observed_s", "samples", "progress", "same_face",
                      "distance", "reason", "window_s", "needed"):
                if k in enr:
                    merged[k] = enr[k]
            self.enrollment = merged
        subj = (sidecar.get("profile") or {}).get("subject")
        if subj and not self.profile.get("subject"):
            self.profile["subject"] = subj     # 只记主体，**不动**确认状态
        return self.enrollment

    def effective_band(self) -> str:
        """本次比对实际使用的参照组：已确认年龄 → 该组；否则全人群兜底 `global`。"""
        band = self.profile.get("band")
        return band if band in AGE_BANDS else GLOBAL_BAND

    def sidecar(self, *, ts: float, frame_id: int, comparison: dict | None = None) -> dict:
        """组装一份符合 `age_contract` 的年龄档案件。"""
        return new_age_state(
            ts=ts, frame_id=frame_id, profile=self.profile,
            enrollment=self.enrollment,
            estimate=(self.estimate.to_estimate_block() if self.estimate else empty_estimate()),
            comparison=comparison,
            stores_face_image=self.stores_face_image,
        )

    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "engine": None if self.engine is None else self.engine.name,
            "engine_placeholder": None if self.engine is None else bool(self.engine.placeholder),
            "engine_available": self.engine is not None,
            "inference_calls": self.inference_calls,
            "inference_refused_locked": self.inference_refused_locked,
            "locked": self.locked(),
            "subject": self.profile.get("subject"),
            "enrollment_state": self.enrollment.get("state"),
            "events": len(self.events),
        }


def _median_signature(samples: list[bytes]) -> bytes | None:
    """逐元素取中位数（对抖动比取第一帧稳）。样本为空/维度不一致时返回 None。"""
    if not samples:
        return None
    n = len(samples[0])
    if any(len(s) != n for s in samples):
        return None
    try:
        import numpy as np  # type: ignore
    except ImportError:
        return samples[len(samples) // 2]
    arr = np.stack([np.frombuffer(s, dtype=np.int8) for s in samples]).astype(np.float32)
    med = np.median(arr, axis=0)
    return np.clip(np.round(med), -127, 127).astype(np.int8).tobytes()


if __name__ == "__main__":  # 自检：python backend/age_infer.py
    import numpy as np

    from age_contract import validate_age_state  # noqa: E402

    cfg = load_config()
    store = AgeProfileStore(cfg)
    print(f"引擎     : {store.engine.name if store.engine else 'none（无可用引擎）'}"
          f"{'  ← 占位引擎，无精度证据' if store.engine and store.engine.placeholder else ''}")
    rng = np.random.default_rng(7)
    img = rng.integers(0, 255, size=(480, 640, 3), dtype=np.uint8)
    bbox = (200, 120, 180, 220)
    sig = FaceSignature.from_image(img, bbox, store.signature_dim)
    print(f"签名     : {len(sig) if sig else 0} 字节 | subject={FaceSignature.subject_id(sig) if sig else '—'}")
    if sig:
        print(f"自距离   : {FaceSignature.distance(sig, sig):.4f}（应为 0）")
    # 走一遍观察窗口：用**逻辑时间**推进，验证 20 s 后自动判定
    for i in range(0, 25):
        store.observe(frame_id=i, ts=float(i), image=img, bbox=bbox,
                      quality_overall=0.9, face_visible=1.0)
    print(f"收录     : state={store.enrollment['state']} "
          f"samples={store.enrollment['samples']} reason={store.enrollment['reason']}")
    # 推断 + 用户确认 → 之后必须停用
    store.maybe_infer(frame_id=1, logical_t=0.0, image=img, bbox=bbox, ts=1.0)
    store.set_manual(31, ts=2.0)
    print(f"确认后   : locked={store.locked()} | inference_allowed={store.inference_allowed()} "
          f"| band={store.profile['band']} | confirmed_by={store.profile['confirmed_by']}")
    side = store.sidecar(ts=3.0, frame_id=2)
    errs = validate_age_state(side)
    print("档案件   :", "通过" if not errs else errs)
    # 【违约留痕】故意在锁定后再调一次推断：schema 必须**当场报错**而不是静默发生。
    store.maybe_infer(frame_id=3, logical_t=1.0, image=img, bbox=bbox, ts=3.0)
    bad = validate_age_state(store.sidecar(ts=3.0, frame_id=3))
    print("锁定后违约:", "被 schema 抓到了（正确）" if bad else "**没抓到（这是缺陷）**")
    print("事件     :", [e["kind"] for e in store.events])
