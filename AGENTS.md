# AGENTS.md — VigiLens / 知倦 AI 协作规范

进入仓库先读本文件。详版规则为 [AI 使用规则](docs/ai_usage.md) 和 [协作与提交规则](docs/contributing.md)，冲突时以这两份为准。当前公开文档入口为 [docs/README.md](docs/README.md)。本轮整理仅压缩旧事实快照，不放宽真实性、契约会签、隐私及人工记录要求。

## 五条底线

1. 不编造测试、实验、资源、时序、BPM 或准确率数字；结论须来自真实运行日志。
2. AI 代码必须逐行理解、本地验证并有相关测试。没跑过不能说跑通、可综合或时序收敛；不得删测试或放宽断言凑绿。
3. 每次协作记录存 `report/llm_log/`，按 `docs/ai_usage.md` 第五节模板由人类亲手写；AI 不得代写协作记录本身。
4. 不上传可识别身份的原始人脸视频到任何外部服务；确需截图仅使用裁剪匿名材料。
5. 仅表述为疲劳风险提示、趋势监测、辅助提示、实验室原型；不得宣称医疗诊断、血氧、血压或未实测精度。

每个结论区分【已验证】【推测】【不确定】。只能引用实际读过的文件，不发明路径、字段、数字或验收结论；文档与代码冲突须如实报告并验证。

## 权威顺序与契约流程

`docs/interface.md` → `config.yaml` → `backend/contract.py` 与 `frontend/mock.js` → `fpga/src/` / 测试台 / 黄金参考 → 目录 README → 方案说明 / 注释。

所有阈值来自 `config.yaml`，不写魔法数字。字段、枚举、寄存器只允许一个定义来源。契约变更必须先改 interface 第 6 节记录，再同步 Python/JS 校验器，运行跨语言检查，群公告并等另两线确认，最后会签升级版本；使用 `contract:` 提交，不能代签。
当前候选与已会签版本须分开写；未会签内容不能作为正式契约升级。

RGB 顺序不能当 OpenCV BGR；ROI 为半开区间；AXI-Stream TUSER 为帧首、TLAST 为行尾。具体字段、寄存器和像素口径以接口契约为准。

## 分工与变更边界

- A：`backend/` 算法/采集/指标/质量/测试及标注/黄金参考，不修改前端或 FPGA/上板实现。
- B：`frontend/` 与后端 API/WebSocket/mock/hub 服务，不修改 A 算法或 FPGA。
- C：`fpga/`、`board/`、相关技能，不修改 PC 后端算法或前端。
- 共享：配置、docs、metrics/scripts、report、skill 与根文件。根 README、config、.gitignore、requirements、pytest.ini、AGENTS 改前先说明。

仅本线目录可直推 main；实验性/可能推翻的修改使用分支。契约遵守上节流程；提交不混入队友改动。不同执行者不得并发控制串口、JTAG 或板卡。

## 当前入口与验证

当前主链为 Mizar-Z7020、USB 相机、PL 像素预处理、PS 原生算法与 HTTP，PC 浏览器显示。状态与限制见 [architecture](docs/architecture.md)、[validation](docs/validation.md)、[limitations](docs/limitations.md)。旧版 JTAG、文件检查、显示历史值和合成回放均不能证明最新持续版物理 SD 启动或准确性。

PowerShell 在仓库根先执行 `. .\env.ps1`，再运行：

```powershell
python metrics/scripts/check_all.py
python -m pytest -q
node frontend/mock.js --selftest
python metrics/scripts/check_frontend_wiring.py
python metrics/scripts/check_video_bypass.py
git diff --check
git status --short
```

跨语言契约检查的临时帧写入忽略的 `metrics/logs/`，不要覆盖已入库证据。根 pytest 默认仅收集 backend/tests；板端、C/C++、HLS、ARM 实测需另外验证，入口见 docs/validation.md。合成或 `landmark_source=stub` 输出不得当作算法/人体结果。

新克隆不带 `.venv`，按 README 创建依赖环境；Windows PowerShell 路径错误不能误判成缺模块。受限环境的 ACL、命名管道或工具链依赖失败须如实区分，不能改断言绕过。MinGW 的 win32 线程模型仅适合 HLS `-fsyntax-only`，不据此宣称完成仿真。

## 提交、证据与编码

提交标签：`A:` / `B:` / `C:` / `shared:` / `contract:` / `docs:` / `repo:`；主题中文保留技术词，正文含“为什么改 / 改了什么 / 怎么验证的”，验证必须真实。AGENTS 用 `docs:` 维护。

不提交 `.venv`、缓存、HLS/Vivado 中间产物、可生成帧二进制、原始视频、个人数据库、模型或工具链；大型位流/启动包单独交付。`metrics/evidence/` 正式证据、`fpga/report/` 报告与黄金 CSV 须保留。

含中文 `.ps1` 使用 UTF-8 BOM；提交消息 UTF-8 无 BOM；Linux shell 用 LF。新增文档名称用英文；历史文件只从 Git/本地备份追溯，不能当现状。

AI 回答给出背景/约束/真实现状/可执行步骤，明确验证方法及不确定性。人类负责真实协作记录和最终验收。
