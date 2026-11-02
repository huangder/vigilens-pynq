"""check_age_sidecar.py —— 年龄档案旁路的可复现检查（跨语言 + 纪律机械检查）。

为什么需要它：本次新增的"年龄档案件"是**旁路载荷**，它没有 `docs/interface.md` 那种
三方会签的契约文档，唯一的准绳就是 `backend/age_contract.py`。而 B 线的 JS 侧
（`frontend/mock.js::validateAgeState`）必须与它得出**同样的判断** —— 这正是
`check_frontend_contract.py` 为 9 字段契约做的事，这里为档案件再做一遍。

它检查 7 类事（每条都对应一个**可被违反的承诺**）：

  T1  schema 自检：`age_contract.py` 自己的空档案件必须合法，且**故意造坏的**必须被抓住
      （锁定后仍推断 / 图像推断自我确认 / 不可比却带数字 / 占位引擎不标注 / 多出字段）
  T2  旁路不污染契约：档案件的顶层字段与冻结契约帧（9 字段）**不重叠**，
      且契约帧本身仍然 `validate_frame` 通过
  T3  参照基线纪律：`data/reference/age_bands.json` 里**解析后可用的**基线必须带出处与核实状态；
      没有数值的条目必须写明缺口说明（不许留白让人以为是漏了）
  T4  数据库纪律：`provenance=stub` 的会话**不进人群统计**；签名以 256 字节存（不是图片）
  T5  耦合模型：门控不过必须"不可比 + 无数字"；老年组眼部证据权重低于年轻组（有文献依据的方向）
  T6  收录与锁定：观察窗口到点自动判定；确认后 `inference_allowed()` 为假、
      且"没问就调"会被 schema 抓住（绊线有效）
  T7  跨语言：把 Python 造出的档案件交给 JS 校验器判一遍，两边结论必须一致
      （JS 侧缺失时 **SKIP 并说明**，不假装通过）

用法（仓库根）：
    .venv\\Scripts\\python.exe metrics/scripts/check_age_sidecar.py
    .venv\\Scripts\\python.exe metrics/scripts/check_age_sidecar.py --json metrics/logs/age_check.json
退出码：0 = 全部通过（SKIP 不算失败，但会在结论里写清楚）；1 = 有 FAIL。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import age_contract as ac          # noqa: E402
import age_infer as ai             # noqa: E402
import fatigue_coupling as fc      # noqa: E402
import fatigue_db as fdb           # noqa: E402
from config import load_config     # noqa: E402
from contract import _TOP_KEYS, validate_frame   # noqa: E402
from mock import mock_frame        # noqa: E402


class Check:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, ok: bool | None, detail: str = "") -> None:
        """ok=True → PASS；False → FAIL；None → SKIP（环境缺东西，不算失败但必须写明）。"""
        self.rows.append((name, "PASS" if ok else ("SKIP" if ok is None else "FAIL"), detail))

    @property
    def fails(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "FAIL"]

    @property
    def skips(self) -> list[tuple[str, str, str]]:
        return [r for r in self.rows if r[1] == "SKIP"]


def t1_schema(ck: Check) -> None:
    ck.add("T1.1 空档案件合法", ac.validate_age_state(ac.new_age_state(ts=1.0, frame_id=0)) == [])
    bad_cases: list[tuple[str, dict]] = []

    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["profile"].update({"age_years": 30, "band": "adult", "source": "manual_input",
                         "confirmed": True, "confirmed_by": "user_manual", "confirmed_at": 1.0,
                         "locked": True, "inference_calls_after_lock": 2})
    bad_cases.append(("锁定后仍推断", s))

    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["profile"].update({"age_years": 24, "band": "young_adult", "source": "image_estimate",
                         "confirmed": True, "confirmed_by": "user_manual", "confirmed_at": 1.0,
                         "locked": True})
    bad_cases.append(("图像推断自我确认", s))

    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["comparison"].update({"comparable": False, "risk": 0.42, "risk_level": "unknown"})
    bad_cases.append(("不可比却带数字", s))

    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["estimate"].update({"engine": "heuristic", "placeholder": False, "band": "adult",
                          "age_years": 40.0, "age_low": 26.0, "age_high": 46.0, "confidence": 0.9})
    bad_cases.append(("占位引擎不自我标注", s))

    s = ac.new_age_state(ts=1.0, frame_id=1)
    s["note"] = "extra"
    bad_cases.append(("多出 schema 外字段", s))

    for name, obj in bad_cases:
        ck.add(f"T1.2 抓得住「{name}」", bool(ac.validate_age_state(obj)),
               "" if ac.validate_age_state(obj) else "**校验器漏检，这是缺陷**")
    ck.add("T1.3 参照组边界（3 岁无组 / 60 岁入老年组）",
           ac.band_for_age(3) is None and ac.band_for_age(60) == "older_adult")


def t2_no_contract_pollution(ck: Check, cfg: dict) -> None:
    store = ai.AgeProfileStore(cfg)
    side = store.sidecar(ts=1.0, frame_id=7)
    side_keys = set(side) - {"ts", "frame_id"}
    frame_keys = set(_TOP_KEYS)
    ck.add("T2.1 档案件与契约帧字段不重叠", not (side_keys & frame_keys),
           f"重叠={sorted(side_keys & frame_keys)}" if side_keys & frame_keys else "")
    ck.add("T2.2 契约帧本身仍合法", validate_frame(mock_frame(0)) == [])
    ck.add("T2.3 档案件恰好 8 个顶层字段", len(side) == 8, f"实际 {len(side)}")


def t3_reference_discipline(ck: Check, norms: fdb.ReferenceNorms) -> None:
    if norms.version == "missing":
        ck.add("T3 参照基线文件存在", None, f"未找到 {norms.path}")
        return
    ck.add("T3.1 基线文件存在且带版本", bool(norms.version) and norms.version != "missing",
           norms.version)
    no_source: list[str] = []
    no_note: list[str] = []
    for band, metrics in norms.bands.items():
        for metric in metrics:
            b = norms.baseline(metric, band)
            if b is not None and not b.source:
                no_source.append(f"{band}.{metric}")
            if b is None and not norms.bands[band][metric].notes:
                no_note.append(f"{band}.{metric}")
    ck.add("T3.2 可用的基线都带出处", not no_source, "缺出处：" + ",".join(no_source))
    ck.add("T3.3 没数值的条目都写了缺口说明", not no_note, "缺说明：" + ",".join(no_note))
    ck.add("T3.4 至少有一条可用数值基线", any(
        norms.baseline(m, b) is not None for b in norms.bands for m in norms.bands[b]))
    ck.add("T3.5 证据段（evidence）与基线段（bands）分开存", bool(norms.evidence),
           f"{len(norms.evidence)} 条证据")
    # 最关键的一条：别的场景/别的口径下测到的数字**不许**混进 bands 当基线
    ck.add("T3.6 年龄敏感度依据在 evidence 段而不在 bands",
           "age_dependent_ocular_sensitivity" in norms.evidence)


def t4_db_discipline(ck: Check, cfg: dict, norms: fdb.ReferenceNorms) -> None:
    with fdb.FatigueDB(":memory:", cfg=cfg) as db:
        n = db.load_reference_into_db(norms) if norms.version != "missing" else 0
        ck.add("T4.1 基线可入库（查询自带出处）", n > 0 or norms.version == "missing",
               f"{n} 行")
        db.start_session(session_id="stub-s", started_at=0.0, age_band="adult",
                         provenance="stub")
        db.add_frame("stub-s", mock_frame(0))
        db.finish_session("stub-s", ended_at=1.0)
        ck.add("T4.2 stub 会话不进人群统计", db.summary()["by_band"] == {},
               f"by_band={db.summary()['by_band']}")
        ck.add("T4.3 stub 会话可在显式要求下看到",
               db.summary(include_stub=True)["by_band"] == {"adult": 1})
        sig = bytes(range(256))
        db.upsert_subject({"subject": "sub-probe", "signature": sig, "at": 1.0})
        row = db.conn.execute("SELECT signature_bytes FROM subjects WHERE subject='sub-probe'"
                              ).fetchone()
        ck.add("T4.4 人脸签名以 256 字节存（不是图片）", row["signature_bytes"] == 256,
               f"{row['signature_bytes']} 字节（原图 640x480x3 = 921600 字节）")
        st = ai.AgeProfileStore(cfg, on_event=lambda ev: db.log_age_event(ev, session_id="stub-s"))
        st.set_manual(31, ts=1.0)
        kinds = [r["kind"] for r in db.conn.execute("SELECT kind FROM age_events")]
        ck.add("T4.5 人工确认写入事件（记录功能）", "manual_confirmed" in kinds, str(kinds))


def t5_coupling(ck: Check, cfg: dict, norms: fdb.ReferenceNorms) -> None:
    good_q = {"overall": 0.92, "light_score": 0.9, "motion_score": 0.05}
    good_face = {"visible": 0.98, "bbox": [200, 120, 180, 220],
                 "pose": {"yaw": -2.0, "pitch": 1.5, "roll": 0.3}}
    tired = {"perclos": 0.34, "long_close_count": 2, "yawn_count": 3, "blink_rate_per_min": 6.0}
    calm = {"perclos": 0.06, "long_close_count": 0, "yawn_count": 0, "blink_rate_per_min": 18.0}

    m = fc.CoupledRiskModel(cfg, norms)
    refused = m.compare(behavior=tired, quality=good_q, face=good_face, band="adult",
                        status="adjust_posture", frame_id=0, ts=0.0)
    ck.add("T5.1 状态不可用 → 不可比且无数字",
           refused["comparable"] is False and refused["risk"] is None
           and refused["risk_level"] == "unknown")

    def run(band: str, beh: dict) -> dict:
        mm = fc.CoupledRiskModel(cfg, norms)
        out: dict = {}
        for i in range(40):
            out = mm.compare(behavior=beh, quality=good_q, face=good_face, band=band,
                             status="normal", frame_id=i, ts=i / 30.0)
        return out

    young, older = run("young_adult", tired), run("older_adult", tired)
    ck.add("T5.2 疲劳输入的风险高于平静输入",
           run("adult", tired)["risk"] > run("adult", calm)["risk"])
    ck.add("T5.3 老年组眼部证据被下调（Cai 2021 方向）", older["risk"] < young["risk"],
           f"young={young['risk']} older={older['risk']}")
    ck.add("T5.4 贡献项都带基线出处与置信度", all(
        c.get("baseline") and c.get("confidence") for c in young["contributions"]))


def t6_enrollment_and_lock(ck: Check, cfg: dict, art: Path | None) -> None:
    img = None
    try:
        import numpy as np
        rng = np.random.default_rng(20261001)
        img = rng.integers(40, 220, size=(480, 640, 3), dtype=np.uint8)
    except ImportError:
        pass
    if img is None:
        ck.add("T6 收录/锁定检查", None, "缺 numpy")
        return
    st = ai.AgeProfileStore(cfg)
    for i in range(25):
        st.observe(frame_id=i, ts=float(i), image=img, bbox=(200, 120, 180, 220),
                   quality_overall=0.9, face_visible=1.0)
    ck.add("T6.1 观察窗口到点自动判定收录", st.enrollment["state"] == "enrolled",
           f"state={st.enrollment['state']} samples={st.enrollment['samples']}")
    st.set_manual(31, ts=1.0)
    ck.add("T6.2 确认后停用图像推测", st.inference_allowed() is False and st.locked())
    st.maybe_infer(frame_id=99, logical_t=9.0, image=img, bbox=(200, 120, 180, 220), ts=9.0)
    errs = ac.validate_age_state(st.sidecar(ts=9.0, frame_id=99))
    ck.add("T6.3 「没问就调」会被 schema 抓住（绊线有效）",
           any("inference_calls_after_lock" in e for e in errs))
    st.reset(ts=10.0)
    ck.add("T6.4 重置后可重新推断（显式动作）",
           st.inference_allowed() is (st.engine is not None))


def t7_cross_language(ck: Check, cfg: dict, probe_dir: str | None = None) -> None:
    """把 Python 造的档案件交给 JS 校验器 —— 两边必须得出同样的结论。"""
    mock_js = REPO_ROOT / "frontend" / "mock.js"
    if not mock_js.exists():
        ck.add("T7 跨语言校验", None, "frontend/mock.js 不存在")
        return
    node = None
    for cand in ("node", "node.exe"):
        try:
            subprocess.run([cand, "--version"], capture_output=True, timeout=20, check=False)
            node = cand
            break
        except (OSError, subprocess.SubprocessError):
            continue
    if node is None:
        ck.add("T7 跨语言校验", None, "本机没有 node —— JS 侧无法验证（不是失败）")
        return

    store = ai.AgeProfileStore(cfg)
    store.set_manual(31, ts=1.0)
    sidecar = store.sidecar(ts=2.0, frame_id=3)
    # ⚠️ 探针文件写到**系统临时目录**，不写进仓库：本仓库在一部分受限沙箱下
    # （`workspace-write` 且 ACL 受限）**子进程无法在仓库子目录里创建文件**，
    # 而 `metrics/logs/` 正是这样的子目录 —— 会导致这个检查报 WinError 5，
    # 看起来像"检查坏了"，其实是环境限制。临时目录在受限与正常环境下都可写。
    tmp = Path(probe_dir) if probe_dir else Path(tempfile.gettempdir())
    tmp.mkdir(parents=True, exist_ok=True)
    side_path = tmp / "_age_sidecar_probe.json"
    side_path.write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")

    script = (
        "const M=require(process.argv[1]);"
        "const fs=require('fs');"
        "const s=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
        "if(typeof M.validateAgeState!=='function'){console.log('MISSING');process.exit(3);}"
        "console.log(JSON.stringify(M.validateAgeState(s)));"
    )
    try:
        proc = subprocess.run([node, "-e", script, str(mock_js), str(side_path)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, cwd=str(REPO_ROOT))
    except (OSError, subprocess.SubprocessError) as e:
        ck.add("T7 跨语言校验", None, f"node 调用失败：{e}")
        return
    out = (proc.stdout or "").strip()
    if proc.returncode == 3 or out == "MISSING":
        ck.add("T7 跨语言校验", None,
               "frontend/mock.js 尚未导出 validateAgeState（B 线侧未接线）—— 这里如实 SKIP")
        return
    py_errs = ac.validate_age_state(sidecar)
    try:
        js_errs = json.loads(out)
    except json.JSONDecodeError:
        ck.add("T7 跨语言校验", False, f"JS 输出无法解析：{out[:120]}")
        return
    ck.add("T7.1 合法的档案件两边都判合法", py_errs == [] and js_errs == [],
           f"python={py_errs} js={js_errs}")
    # 再造一个坏档案件：两边都必须抓住
    broken = json.loads(json.dumps(sidecar))
    broken["profile"]["inference_calls_after_lock"] = 1
    side_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    proc2 = subprocess.run([node, "-e", script, str(mock_js), str(side_path)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60, cwd=str(REPO_ROOT))
    try:
        js2 = json.loads((proc2.stdout or "").strip())
    except json.JSONDecodeError:
        ck.add("T7.2 坏档案件两边都抓住", False, f"JS 输出无法解析：{proc2.stdout[:120]}")
        return
    ck.add("T7.2 坏档案件两边都抓住（锁定后仍推断）",
           bool(ac.validate_age_state(broken)) and bool(js2),
           f"python={len(ac.validate_age_state(broken))} 条 js={len(js2)} 条")


def t8_schema_file_matches_code(ck: Check) -> None:
    """`data/fatigue_db/schema.sql`（入库给人看的那份）必须与代码里的建表语句**语义等价**。

    为什么是"语义"而不是"逐字节"：文件里有给人读的中文注释，逐字节比对会因为
    注释措辞变化而红，那种红没人会去修、最后就被忽略了。这里比的是
    **sqlite_master 里的建表语句**（去掉行注释、压平空白后再比），
    也就是"表/列/类型/约束是否一致"—— 那才是真正的不变量。
    """
    import sqlite3

    path = REPO_ROOT / "data" / "fatigue_db" / "schema.sql"
    if not path.exists():
        ck.add("T8 入库的 schema.sql 与本文件语义一致", None, f"未找到 {path}")
        return

    def normalize(sql: str) -> str:
        lines = []
        for raw in sql.splitlines():
            line = raw.split("--", 1)[0]
            if line.strip():
                lines.append(" ".join(line.split()))
        return " ".join(lines)

    def objects(script: str) -> dict[str, str]:
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(script)
            rows = con.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
        finally:
            con.close()
        return {f"{t}:{n}": normalize(s or "") for t, n, s in rows}

    file_objs = objects(path.read_text(encoding="utf-8"))
    code_objs = objects(fdb.schema_sql())
    only_file = sorted(set(file_objs) - set(code_objs))
    only_code = sorted(set(code_objs) - set(file_objs))
    diff = [k for k in set(file_objs) & set(code_objs) if file_objs[k] != code_objs[k]]
    ck.add("T8 入库的 schema.sql 与本文件语义一致",
           not (only_file or only_code or diff),
           f"仅文件有={only_file} 仅代码有={only_code} 内容不同={diff}")
    # frames 表的类型也要如实（frame_id / bbox / 计数是整数）
    frames_sql = file_objs.get("table:frames", "")
    ck.add("T8.2 frames 的整数列没有写成 REAL",
           "frame_id INTEGER" in frames_sql and "bbox_x INTEGER" in frames_sql
           and "long_close_count INTEGER" in frames_sql)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="年龄档案旁路的可复现检查")
    ap.add_argument("--json", default=None, help="把结果写成 JSON（受限沙箱下请指到可写目录）")
    ap.add_argument("--probe-dir", default=None,
                    help="跨语言校验用的临时探针目录（默认系统临时目录）")
    args = ap.parse_args(argv)

    cfg = load_config()
    norms = fdb.load_reference(cfg=cfg)
    ck = Check()

    print("=" * 78)
    print("VigiLens 年龄档案旁路检查（age sidecar）")
    print("=" * 78)
    print(f"参照基线: {norms.path}  版本 {norms.version}  核实日 {norms.checked_on}\n")

    t1_schema(ck)
    t2_no_contract_pollution(ck, cfg)
    t3_reference_discipline(ck, norms)
    t4_db_discipline(ck, cfg, norms)
    t5_coupling(ck, cfg, norms)
    t6_enrollment_and_lock(ck, cfg, None)
    t7_cross_language(ck, cfg, args.probe_dir)
    t8_schema_file_matches_code(ck)

    for name, status, detail in ck.rows:
        line = f"  [{status}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)

    n_pass = sum(1 for r in ck.rows if r[1] == "PASS")
    n_fail = len(ck.fails)
    n_skip = len(ck.skips)
    print()
    print(f"RESULT: {'PASS' if n_fail == 0 else 'FAIL'} "
          f"({n_pass}/{len(ck.rows)} PASS, {n_fail} FAIL, {n_skip} SKIP)")
    if n_skip:
        print("SKIP 项**不算通过**，逐条原因见上面 —— 引用本检查时请一并说明。")
    if n_fail:
        print("失败项：")
        for name, _, detail in ck.fails:
            print(f"  - {name} {detail}")

    if args.json:
        out = REPO_ROOT / args.json
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "checked_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
            "reference_version": norms.version,
            "pass": n_pass, "fail": n_fail, "skip": n_skip,
            "rows": [{"name": n, "status": s, "detail": d} for n, s, d in ck.rows],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出 {out}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
