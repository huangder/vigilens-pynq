# 协作与提交规则

本文保留原协作规则，移除已过期的成员权限、提交基线与本机代理配置快照。数据真实性与人工协作记录要求见 [AI 使用规则](ai_usage.md)。

## 分工与共享文件

| 线 | 负责范围 | 避免修改 |
|---|---|---|
| A 算法/后端 | `backend/` 算法/采集/指标/质量/测试，`data/annotations/`、`data/golden/` | 前端、FPGA、上板实现 |
| B 前端/服务 | `frontend/`，`backend/api.py`、`websocket.py`、`mock.py`、`hub.py` 等服务文件 | A 算法文件、FPGA |
| C FPGA/板端 | `fpga/`、`board/`、`skill/fpga_hls_c_line.md` | PC 后端算法、前端 |
| 共享 | 根配置、README、依赖与仓库设置，`docs/`、`metrics/scripts/`、`report/`、`skill/` | 修改前先说明影响 |

根 `README.md`、`config.yaml`、`.gitignore`、`requirements.txt`、`pytest.ini` 和 `AGENTS.md` 改前先说一声。
仅修改本线目录可直接推 main；实验性/可能推翻的修改使用分支；契约变更遵循下一节，不以方便为由跳过。

## 契约流程

权威顺序：`docs/interface.md` → `config.yaml` → `backend/contract.py` / `frontend/mock.js` → 硬件源码/测试台/黄金参考 → 各 README → 方案说明/注释。发现冲突须报告、验证并同步修正，不能自行升级会签版本。

1. 先修改 `docs/interface.md`，在第 6 节登记日期、版本、修改人与影响。
2. 同步 Python 契约与 JS 校验器，以及后续业务实现；字段/枚举/偏移不另立来源。
3. 运行跨语言与相关检查。
4. 群公告，等待另两线确认“收到、不冲突”后再作正式契约提交。
5. 会签后升级版本；未会签候选须显式保留候选状态，不代签。

## 提交与验证

提交信息使用以下格式；标签为 `A:`、`B:`、`C:`、`shared:`、`contract:`、`docs:` 或 `repo:`。主题用中文，技术名词保留英文；一个提交只做一件事，验证部分不能空。

```text
docs: 精简公开使用说明

为什么改：
改了什么：
怎么验证的：真实命令、输出和未验证范围
```

提交前运行 [validation.md](validation.md) 的项目总检查、pytest 与前端检查，相关板端改动另做对应测试。契约检查可让 Node 输出到忽略的 `metrics/logs/`，不要覆盖已归档的 `metrics/evidence/js_frames.jsonl`。PowerShell 5.1 的 `>` 默认编码不适合生成 UTF-8 证据，使用明确的 UTF-8 写法。

检查 `git diff --check` 和 `git status --short`，不得混入其他人的变更、凭据、原始人脸媒体、虚拟环境、模型、数据库或构建缓存。`metrics/evidence/` 正式证据、黄金参考 CSV 和 `fpga/report/` 报告须保留。测试基线不得通过删测试或放宽断言凑绿。

Git 作者使用实际贡献者账号与已验证的邮箱；不虚构他人贡献或签名。推送前获取远端变更并正常合并，不强推覆盖队友工作。

## 人工协作记录与文件编码

每次 AI 协作按 [ai_usage.md](ai_usage.md) 第五节模板存入 `report/llm_log/`；提示词、回答、自我纠错、采用内容与验证结论须由人类亲手记录，AI 不得代写。

PowerShell 5.1 中含中文的 `.ps1` 保存为 UTF-8 BOM；提交消息文件使用 UTF-8 无 BOM；Linux shell 脚本使用 LF。文档路径使用英文，新增名称优先小写字母、数字、下划线或连字符，`README.md` 等工具约定名称保留。
