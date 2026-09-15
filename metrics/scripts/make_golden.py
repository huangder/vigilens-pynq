"""make_golden.py —— 生成并**锁定** A10 黄金结果（`data/golden/`，入库）。

为什么需要它：
    《02》A10 要求"对 4 段标准视频生成黄金 CSV 并锁版本"。所谓"锁版本"不只是存一份 CSV，
    而是让后人能回答："这份黄金结果，是在**哪段视频 + 哪份 config + 哪次代码**下跑出来的？"
    所以每段视频除了产物 CSV，还写一份 `_lock.json`，把三样东西的指纹都记下来：
      输入视频 SHA256、`config.yaml` SHA256、git HEAD。
    任何一样变了，重新跑就会得到不同的指纹 —— 一眼能看出"黄金结果过期了"。

用法（仓库根）：
    python metrics/scripts/make_golden.py                        # data/raw 下 4 段全跑
    python metrics/scripts/make_golden.py --videos blink turn    # 只跑其中几段
    python metrics/scripts/make_golden.py --source <视频> --name smoke   # 单文件（自测/一次性）
    python metrics/scripts/make_golden.py --verify               # 每段跑两次，要求逐字节一致
退出码：0 = 全部成功；1 = 有失败或缺输入。

⚠️ `_lock.json` **不含时间戳** —— 同样的输入+配置+代码必须得到逐字节相同的锁定文件，
   否则每次重跑都会把工作区搞脏（这条纪律与 `check_a_line_p1_all.py` 的证据归档一致）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VIDEOS = ["still", "blink", "yawn", "turn"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> str | None:
    """当前 commit（拿不到就返回 None —— 不影响黄金结果本身）。"""
    out = _git(["rev-parse", "HEAD"])
    return out.strip() if out else None


def _git(args: list[str]) -> str | None:
    """跑一条 git 子命令；git 不可用时返回 None。"""
    for git in (REPO_ROOT / ".tools" / "git" / "cmd" / "git.exe", "git"):
        try:
            r = subprocess.run([str(git), *args], cwd=str(REPO_ROOT),
                               capture_output=True, text=True, timeout=20)
            if r.returncode == 0:
                return r.stdout
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def backend_dirty_paths() -> list[str] | None:
    """`backend/` 下是否有未提交改动。

    为什么锁定文件需要它：只记 git HEAD 的话，**工作区脏的时候这个 HEAD 代表不了跑出黄金结果的代码**，
    别人照 HEAD 重跑可能得到不同结果。返回 None 表示查不到（git 不可用）。
    """
    out = _git(["status", "--porcelain", "--", "backend"])
    if out is None:
        return None
    return sorted(ln[3:].strip() for ln in out.splitlines() if ln.strip())


def run_pipeline(video: Path, csv_out: Path, work: Path) -> tuple[int, dict | None, str]:
    """跑一次 pipeline，返回 (退出码, summary, 末行输出)。"""
    summary_path = work / "summary.json"
    json_path = work / "last.json"
    cmd = [sys.executable, str(REPO_ROOT / "backend" / "run_pipeline.py"),
           "--source", str(video), "--quiet",
           "--csv", str(csv_out), "--json", str(json_path), "--summary", str(summary_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(REPO_ROOT))
    out = (r.stdout or "") + (r.stderr or "")
    summary = None
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    tail = [ln for ln in out.splitlines() if ln.strip()]
    return r.returncode, summary, (tail[-1].strip() if tail else "")


def make_one(video: Path, name: str, out_dir: Path, verify: bool) -> bool:
    """生成一段视频的黄金结果与锁定文件。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{name}_metrics.csv"
    lock_path = out_dir / f"{name}_lock.json"
    cfg_path = REPO_ROOT / "config.yaml"

    with tempfile.TemporaryDirectory(prefix="golden_") as td:
        work = Path(td)
        code, summary, tail = run_pipeline(video, csv_path, work)
        if code != 0 or not csv_path.exists():
            print(f"  [FAIL] {name}: 退出码 {code}  {tail}")
            return False

        if verify:
            second = work / "second.csv"
            code2, _, _ = run_pipeline(video, second, work)
            if code2 != 0 or second.read_bytes() != csv_path.read_bytes():
                print(f"  [FAIL] {name}: 两次运行的 CSV 不是逐字节一致（《02》A3 的可复现口径）")
                return False

    rows = max(0, len(csv_path.read_text(encoding="utf-8").splitlines()) - 1)
    lock = {
        "artifact": "A10 golden metrics（锁版本）",
        "note": "不含时间戳：同样的输入+配置+代码必须得到逐字节相同的本文件。",
        "video": {"path": str(video.relative_to(REPO_ROOT)).replace("\\", "/"),
                  "sha256": sha256_file(video), "bytes": video.stat().st_size},
        "config": {"path": "config.yaml", "sha256": sha256_file(cfg_path)},
        # 只记 HEAD 是不够的：工作区脏的时候，HEAD 代表不了真正跑出这份黄金结果的代码。
        "code": {"git_head": git_head(), "backend_dirty_paths": backend_dirty_paths()},
        "run": {"logical_fps": (summary or {}).get("logical_fps"),
                "frames_processed": (summary or {}).get("frames_processed"),
                "landmark_source": (summary or {}).get("landmark_source")},
        "golden": {"csv": str(csv_path.relative_to(REPO_ROOT)).replace("\\", "/"),
                   "sha256": sha256_file(csv_path), "rows": rows},
    }
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  [OK]   {name}: {rows} 行 -> {csv_path.name}"
          f"（{lock['run']['landmark_source']}，已写 {lock_path.name}）")
    return True


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="生成并锁定 A10 黄金结果（写 data/golden/）")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out-dir", default="data/golden")
    ap.add_argument("--videos", nargs="*", default=None, help=f"默认 {DEFAULT_VIDEOS}")
    ap.add_argument("--source", default=None, help="单文件模式：直接指定视频路径")
    ap.add_argument("--name", default=None, help="单文件模式下的输出名（默认取文件名）")
    ap.add_argument("--verify", action="store_true", help="每段跑两次并要求逐字节一致")
    args = ap.parse_args()

    out_dir = REPO_ROOT / args.out_dir
    print("=== A10 黄金结果：生成 + 锁版本 ===")
    print(f"输出目录: {out_dir.relative_to(REPO_ROOT)}"
          + ("   [--verify 已开：每段跑两次]" if args.verify else ""))
    print()

    targets: list[tuple[Path, str]] = []
    if args.source:
        src = Path(args.source)
        src = src if src.is_absolute() else (REPO_ROOT / src)
        if not src.exists():
            print(f"[FAIL] 找不到视频：{src}")
            return 1
        targets.append((src, args.name or src.stem))
    else:
        raw = REPO_ROOT / args.raw
        for nm in (args.videos or DEFAULT_VIDEOS):
            v = raw / f"{nm}.mp4"
            if not v.exists():
                print(f"  [SKIP] {nm}.mp4 不存在（见 data/README.md 的录制规范）")
                continue
            targets.append((v, nm))

    if not targets:
        print("[FAIL] 没有任何可处理的视频。先按 data/README.md 录 4 段标准视频，")
        print("       或先用 --source <视频> 试跑一段（例如 metrics/logs/_smoke.mp4）。")
        return 1

    # ⚠️ 不要写 all(make_one(...) for ...)：all() 对生成器**短路**，
    #    第一段失败后面的视频就再也不会被处理，而输出里看不出这点。
    #    这里先全部跑完再汇总。
    results = [(nm, make_one(v, nm, out_dir, args.verify)) for v, nm in targets]
    failed = [nm for nm, good in results if not good]
    ok = not failed
    print()
    if ok:
        print(f"[完成] {len(targets)} 段黄金结果已写入 {out_dir.relative_to(REPO_ROOT)}"
              "（含 _lock.json：输入视频 / config.yaml / git HEAD 三样指纹）")
        print("       记得把 data/golden/ 一并提交 —— 它是要锁版本的正式产物。")
        return 0
    print(f"[FAIL] {len(failed)}/{len(targets)} 段未能生成黄金结果：{', '.join(failed)}")
    print("       其余段已正常产出（不是全部作废）—— 先修好失败段再整体重跑。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
