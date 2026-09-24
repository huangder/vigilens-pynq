# env.ps1 —— 知倦 / VigiLens 的仓库会话环境
#
# 用法（两种都行）：
#     . .\env.ps1                 # 在仓库根
#     . D:\Desktop\AMD\env.ps1    # 从任意目录（脚本自己解析仓库根）
#
# 它做什么：
#     1. 把 `.venv\Scripts` 排到 PATH 最前 —— 这样直接敲 `python` 就是项目解释器
#     2. 把当前目录切到仓库根 —— 文档里所有命令都假设"在仓库根执行"
#     3. 导出 `$env:VIGILENS_ROOT`
#
# 只影响**当前 PowerShell 会话**，不改系统/用户环境变量。
#
# ---------------------------------------------------------------------------
# 为什么会有这个文件（2026-09-24 补）：
#   在此之前，`AGENTS.md` / `backend/README.md` / `backend/A_LINE_DEV_STEPS.md` /
#   `docs/11_测试方案与执行步骤.md` / `metrics/scripts/check_a_line_*.py`
#   都写着"先 `. .\env.ps1`"，但这个文件**从来不在仓库里**
#   （`git log --all -- env.ps1` 为空、`git ls-files env.ps1` 为空）——
#   它是开发机上的本机文件。
#   结果：**任何新机器/新同事照文档做都会失败**（报 `'. .\env.ps1' 不是可识别的命令`）。
#   现在把它作为**可提交的通用脚本**补上：路径一律由 `$PSScriptRoot` 推导，
#   不含任何机器专属信息，因此在任何机器上都成立。
#
#   它同时顺带解决另一类高频故障：`.venv\Scripts\python.exe` 这种**裸相对路径**
#   只在仓库根有效；在别处执行时 PowerShell 会给出极具误导性的报错
#   （`无法加载模块 ".venv"`），而不是"文件不存在"。
#   先点源本文件（它会切到仓库根），这类报错就不会再出现。
# ---------------------------------------------------------------------------

$RepoRoot = $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = (Get-Location).Path }

$env:VIGILENS_ROOT = $RepoRoot

# ---- 1) 项目解释器排到 PATH 最前 ------------------------------------------
$VenvScripts = Join-Path $RepoRoot ".venv\Scripts"
$VenvPython  = Join-Path $VenvScripts "python.exe"

if (Test-Path $VenvPython) {
    # 已经排过就不重复插（反复点源本文件不会把 PATH 撑长）
    if ($env:PATH -notlike "*$VenvScripts*") {
        $env:PATH = "$VenvScripts;$env:PATH"
    }
    $env:VIRTUAL_ENV = Join-Path $RepoRoot ".venv"
} else {
    Write-Warning "找不到 $VenvPython"
    Write-Warning "  .venv/ 在 .gitignore 里，**新克隆的仓库不会带它**，需要先建："
    Write-Warning "      python -m venv .venv"
    Write-Warning "      .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
}

# ---- 2) 切到仓库根 --------------------------------------------------------
# 文档里所有命令都假设"在仓库根执行"；不切的话裸相对路径会在别处报出
# `无法加载模块 ".venv"` 这种与真实原因无关的错误。
if ((Get-Location).Path -ne $RepoRoot) {
    Set-Location $RepoRoot
}

# ---- 3) 提示 -----------------------------------------------------------------
Write-Host "[env] 仓库根 : $RepoRoot" -ForegroundColor Cyan
if (Test-Path $VenvPython) {
    Write-Host "[env] 解释器 : $VenvPython" -ForegroundColor Cyan
    Write-Host "[env] 现在可以直接敲  python metrics/scripts/check_all.py" -ForegroundColor Cyan
} else {
    Write-Host "[env] 解释器 : 缺失（见上面的提示）" -ForegroundColor Yellow
}
