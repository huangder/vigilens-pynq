"""pytest 公共配置。

1) sys.path：让 `pytest backend/tests`（仓库根执行）与 `pytest`（backend/ 执行）
   两种方式都能 import 到本项目模块，不依赖 pip install -e 或 pyproject。

2) workdir fixture：**不用 pytest 自带的 tmp_path**。
   原因（实测踩过）：pytest 的 tmp_path 会在系统临时目录建 `pytest-of-<user>/pytest-N`，
   在受限沙箱/加密盘/受限 ACL 的机器上会出现"建得出来但列不出、删不掉"的目录
   （WinError 5），随后**整批测试直接 ERROR**，而这不是项目代码的问题。
   改成仓库内 `metrics/evidence/_pytest_tmp/`，同时满足三件事：
     - 任何权限环境都能跑；
     - 产物留在仓库里，出问题可以直接翻；
     - 已在 .gitignore 里排除，不会脏化提交。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "backend"

for p in (str(REPO_ROOT), str(BACKEND)):
    if p not in sys.path:
        sys.path.insert(0, p)

SCRATCH_ROOT = REPO_ROOT / "metrics" / "evidence" / "_pytest_tmp"


@pytest.fixture
def workdir(request: pytest.FixtureRequest) -> Path:
    """每个测试一个干净的仓库内临时目录。"""
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", request.node.name)[:80]
    d = SCRATCH_ROOT / safe
    if d.exists():
        for child in sorted(d.rglob("*"), reverse=True):
            try:
                child.unlink() if child.is_file() else child.rmdir()
            except OSError:
                pass
    d.mkdir(parents=True, exist_ok=True)
    return d
