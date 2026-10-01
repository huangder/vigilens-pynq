# 年龄分层疲劳参考基线 & 年龄推断 —— 可引用来源调研笔记

> 项目：**知倦 / VigiLens** —— AMD/Xilinx Zynq 无接触疲劳趋势监测终端（2026 嵌入式芯片与系统设计竞赛 · AMD 赛道）
> 本文用途：为 (a) **年龄分层疲劳参考数据库** 与 (b) **摄像头年龄推断** 两个新需求提供**真实、可引用**的来源。
> 撰写方式：全部通过联网检索 + 实际打开页面核实；**未核实的数字一律不写**，而是集中列在 §8 与 §10。
> ⚠️ 本文**不是**项目实测结论，是**外部文献/开源资料调研**。项目自身的阈值仍以 `config.yaml` 为唯一来源（见 `AGENTS.md` §3）。

---

## 0. 置信度约定与调研方法

### 0.1 置信度标签（严格按本项目的自检三条）

| 标签 | 含义 |
|---|---|
| **【已验证】** | 我**真的打开了该页面 / 该 API 记录**，并在其中**读到了**这个数字或结论 |
| **【推测】** | 数字来自二手来源（综述、教程、搜索引擎对页面的抽取），**或**我**没能打开**一手页面 |
| **【不确定】** | 无法核实，或存在互相矛盾的说法 —— **不要拿去用** |

### 0.2 本次调研的环境限制（影响可核实范围，务必知道）

| 限制 | 具体表现 | 影响 |
|---|---|---|
| **GitHub 全域不可达** | `github.com` / `raw.githubusercontent.com` 在本机被 DNS 解析到非公网 IP（fake-IP / hosts 加速器），工具直接拒绝访问 | **所有模型仓库的 LICENSE 文件、权重文件大小、"权重能否下载"这三类问题，本文基本无法给【已验证】** |
| **PubMed 直连被拦** | `pubmed.ncbi.nlm.nih.gov` 返回 203 + "Cookies must be enabled" 或 reCAPTCHA | 改用 **Europe PMC REST API**（`ebi.ac.uk/europepmc/webservices/rest/...`）取权威题录与摘要 |
| **PDF 无法直接抓取** | `web_fetch` 返回 `unsupported content type "application/pdf"`；PDF 正文要靠检索工具的抽取 | 部分 PDF 内文只能标【推测】 |
| **检索配额耗尽** | 调研后半段 Firecrawl 免费额度用尽（HTTP 429），后续检索不可用 | §7（开源项目）与部分数据集**未能完成核实** |
| **本机 shell 无外网** | PowerShell `Invoke-RestMethod` 连接失败 | 无法在本地过滤/落盘，只能靠工具输出 |

### 0.3 一个必须写进团队文档的前提

> **文献层面最反直觉、但对本项目最重要的一条结论是：眨眼"频率"随年龄的变化并不稳健，而眨眼"时长/运动学"随年龄变化更稳健；而 PERCLOS 对睡眠剥夺的敏感性在老年组可能消失。**
> 也就是说：**"按年龄分层做参考带"这件事有文献支持，但被分层的量要选对** —— 分 PERCLOS / 闭眼时长比单纯分"眨眼次数/分"更有依据。详见 §1 与 §3。

---

## 1. 眨眼频率（blink rate）与眨眼时长（blink duration）随年龄的变化

### 1.1 结论速查

| 量 | 是否随年龄变化 | 依据强度 |
|---|---|---|
| 静息/注视下**眨眼频率** | **多项研究未发现年龄差异** | 较强（Bentivoglio 1997 n=150；Sun 1997） |
| **任务**对眨眼频率的影响 | **远大于**年龄的影响（阅读 < 静息 < 交谈） | 很强（Bentivoglio 1997；Doughty 2001） |
| **眨眼幅度 / 峰值速度** | **随年龄下降**（自发性眨眼比随意眨眼更明显） | 较强（Sun 1997；Sforza 2008） |
| **眨眼时长 / 闭眼时长** | 老年人**变慢**；睡眠剥夺会显著**变长** | 中等（Sforza 2008 给速度；Wilkinson 2013 给睡眠剥夺下的时长） |
| **性别**对眨眼频率的影响 | 女性偏高，但**仅在特定任务下显著** | 中等（Bentivoglio 1997 仅阅读时；Sforza 2008 显著） |

### 1.2 【已验证】Bentivoglio et al. 1997 —— 150 人正常眨眼频率基准

- **来源标题**：Analysis of blink rate patterns in normal subjects.
- **URL（题录与摘要）**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:9399231&resultType=core&format=json>
- **DOI**：<https://doi.org/10.1002/mds.870120629>
- **发表**：*Movement Disorders* 1997;12(6):1028–1034（Mov Disord）
- **样本**：150 名健康志愿者（70 男 / 80 女），年龄 **35.9 ± 17.9 岁，范围 5–87 岁**
- **数字（我从摘要中逐字读到）**：
  - 静息（rest）：**17 blinks/min**
  - 交谈（conversation）：**26 blinks/min**
  - 阅读（reading）：**4.5 blinks/min**
  - 相对静息：阅读 **−55.08%**（p < 1×10⁻¹⁵）；交谈 **+99.70%**（p < 1×10⁻⁹）
  - 相对阅读：交谈 **+577.8%**（p < 1×10⁻¹⁷）
  - 分布：最佳拟合为 **log-normal**，上尾近似正态
  - 眼色与戴眼镜**不影响**眨眼频率；**女性仅在阅读时**高于男性
  - **"No age-related differences were found."（未发现年龄相关差异）** —— 原句
  - 最常见模式 交谈 > 静息 > 阅读：**101/150（67.3%）**；静息 > 交谈 > 阅读：**34（22.7%）**；交谈 > 阅读 > 静息：**12（8.0%）**
- **对本项目的含义**：**"眨眼次数/分"不能直接按年龄分带**；但**必须按任务/场景分带**。VigiLens 是桌面/终端场景，"阅读/注视屏幕"对应表中的 **4.5 blinks/min** 量级，而"交谈"是 26 —— 差 5.8 倍。**场景归一化的优先级高于年龄归一化。**

### 1.3 【已验证】Doughty 2001 —— 95% 置信区间式的"正常成人"参考带

- **来源标题**：Consideration of three types of spontaneous eyeblink activity in normal humans: during reading and video display terminal use, in primary gaze, and while in conversation.
- **URL（题录与摘要）**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:11700965&resultType=core&format=json>
- **DOI**：<https://doi.org/10.1097/00006324-200110000-00011>
- **发表**：*Optometry and Vision Science* 2001;78(10):712–725
- **方法**：对 **75 年**文献的回顾性评估 + 与年轻成人受试者的同条件对照
- **数字（95% CI，正常成人；摘要原文）**：
  - **reading-SEBR：1.4 – 14.4 blinks/min**
  - **primary gaze-SEBR：8.0 – 21.0 blinks/min**
  - **conversational-SEBR：10.5 – 32.5 blinks/min**
  - 电生理法（electrophysiological）测得的 SEBR **略高于**观察法
- **作者结论**：**不应笼统地说"正常眨眼频率是多少"**，报告时必须前缀实验条件（reading / primary gaze in silence / conversational）
- **对本项目的含义**：这是一条**可直接抄进 `docs/` 的方法论纪律** —— VigiLens 的 `blink_rate_per_min` 必须在 UI/文档里注明采集场景，否则数字无意义。这也解释了为什么 `config.yaml` 的 `window_seconds: 30` 滑窗眨眼率天然波动大。

### 1.4 【已验证】Sun et al. 1997 —— 老年人眨眼运动学（幅度/速度下降，频率不变）

- **来源标题**：Age-related changes in human blinks. Passive and active changes in eyelid kinematics.
- **URL（题录与摘要）**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=EXT_ID:9008634&resultType=core&format=json>
- **PMID**：9008634
- **发表**：*Investigative Ophthalmology & Visual Science (IOVS)* 1997;38(1):92–99
- **方法**：电磁搜索线圈法（electromagnetic search coil），按**每十年**分组，覆盖 **40–89 岁**
- **结果（摘要原文）**：
  - 眨眼**平均幅度**与**峰值速度**随年龄**下降**（自发性眨眼下降幅度 > 随意眨眼）
  - 下降**部分**可归因于外周因素：**睑裂宽度变窄**
  - 自发性眨眼**下降相 main sequence 斜率**随年龄下降
  - **"By contrast, blink rate and the coordination of movements of the two eyelids—blink conjugacy—exhibited no change."（相反，眨眼频率与双眼协同性无变化）**
- **对本项目的含义**：老年人闭眼"慢而浅"。如果 EAR 阈值是**固定**的，老年人可能**更难被判为"完全闭眼"**（因为幅度小、睑裂窄），从而**低估 PERCLOS** —— 这是一个**具体的、可写进风险表的算法偏差假设**（属于【推测】，需本项目自测验证）。

### 1.5 【已验证】Sforza et al. 2008 —— 年轻组 vs 老年组的光电运动学对照

- **来源标题**：Spontaneous blinking in healthy persons: an optoelectronic study of eyelid motion.
- **URL（题录与摘要）**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%22spontaneous%20blinking%22%20AND%20%22optoelectronic%22&resultType=core&format=json>
- **DOI**：<https://doi.org/10.1111/j.1475-1313.2008.00577.x>
- **发表**：*Ophthalmic and Physiological Optics* 2008;28(4):345–353
- **样本**：年轻组 20–30 岁（13 男 / 12 女）；老年组 **>50 岁**（10 男 / 9 女）
- **数字（摘要原文）**：
  - 自发性眨眼频率：**女性 19 vs 男性 11 blinks/min**，女性显著更高
  - **老年女性比年轻女性眨眼更频繁**
  - 完全（或几乎完全）闭眼比例：**年轻男性 44% 的眨眼**；年轻与老年女性的闭眼更多落在最大位移的 **51–75%** 区间
  - **闭眼与睁眼的最大速度随年龄下降：老年受试者的眼睑运动比年轻受试者慢约 80–70%**
  - 所有受试者中**闭眼比睁眼快 40–47%**；女性快于男性；眼睑位移年轻组大于老年组
- **对本项目的含义**：
  1. **性别差异（19 vs 11）比年龄差异更显眼** —— 如果要做参考带，**性别可能与年龄同等重要**（这点必须写进团队文档，否则会漏掉一个比年龄更大的混淆因素）。
  2. **"老年女性眨眼更多"与 Bentivoglio 1997"无年龄差异"并不矛盾**：前者在 Sforza 的分组设计下显著，后者在 150 人连续年龄回归下不显著。**这正是"文献不一致"的典型例子**，按 `AGENTS.md` §3 的做法应**如实报告不一致，不要自行拍板**。

### 1.6 【已验证】"清醒时眨眼时长 < 200 ms；睡眠剥夺后出现 > 500 ms 的慢闭眼"

- **来源标题**：The Accuracy of Eyelid Movement Parameters for Drowsiness Detection
- **URL**：<https://pmc.ncbi.nlm.nih.gov/articles/PMC3836343/>
- **DOI**：<https://doi.org/10.5664/jcsm.3278>
- **发表**：*Journal of Clinical Sleep Medicine* 2013;9(12):1315–1324（Wilkinson VE, Jackson ML, Westlake J, Stevens B, Barnes M, Swann P, Rajaratnam SMW, Howard ME）
- **原文（Introduction）**：*"While blink duration in rested conditions lasts for less than 200 ms, sleep deprivation results in increased blink duration, episodes of slow eye closure lasting more than 500 ms, and increased proportion of time the eyes are closed."*
- **同一节**：*"The proportion of time the eyes are at least 80% closed (PERCLOS) increases in drowsy participants during task performance..."*
- **样本**：健康受试者 **18–70 岁**，n = 33 人 / 71 个数据点；随机交叉设计（正常睡眠 vs 限制卧床 4 h）
- **性能数字（摘要原文，**注意每个数字都带基准**）**：
  - IED（inter-event duration，等价"平均闭眼时长"）与 −AVR：**ROC AUC 0.73–0.83**（p < 0.05）
  - IED 对 **PVT ≥3 lapses/min**：**灵敏度 71% / 特异度 88%**
  - IED 对 **PVT ≥5 lapses/min**：**灵敏度 100% / 特异度 86%**
  - 复合指标 JDS（Johns Drowsiness Scale）：3 lapses → **77% / 85%**；≥5 lapses → **100% / 83%**
- **⚠️ 一处存疑（照抄原文、不替它圆）**：该文对 `%LC`（Percent Long Closures）的定义写作 *"proportion of time eyes are fully closed > 10 ms"*。**10 ms 在生理上像是"1000 ms"的排版/OCR 讹误**（若真是 10 ms，"long closure"将等同于所有眨眼）。我**无法打开 PDF 原页确认**，故：**该定义标【不确定】，不要引用**。
- **对本项目的含义**：**"-AVR / IED / 闭眼时长"这一类"时长型"指标有明确的 ROC 报告，而"眨眼次数"没有** —— 这为 §1.1 的结论提供了第二个独立支撑，也为 `behavior_metrics.py` 的指标取舍提供了文献依据。

### 1.7 本领域的检索式（可复现）

```
Doughty 2001 blink rate survey normative consideration
Bentivoglio 1997 spontaneous blink rate physiology normal values
Sun 1997 age related blink rate study
"spontaneous blinking" AND "optoelectronic"          # Europe PMC
TITLE:"blink rate" AND TITLE:"age"                    # Europe PMC
TITLE:"blink rate" AND (children OR child)            # Europe PMC
```
- 用到的机器可读入口：`https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=<QUERY>&resultType=core|lite&format=json`

---

## 2. PERCLOS 作为困倦指标：出处、定义与阈值

### 2.1 结论速查

| 问题 | 答案 | 置信度 |
|---|---|---|
| PERCLOS 谁提出的？ | **Wierwille 等，1994 年驾驶模拟器研究** | 【已验证】 |
| P70 / P80 / EYEMEAS 是什么？ | PERCLOS 的三种变体（70% 闭、80% 闭、均方百分比） | 【推测】 |
| "至少 80% 闭合"这个定义谁定的？ | Wierwille 1994，被 FHWA/NHTSA 正式采纳 | 【已验证】 |
| 谁做了效度验证？ | Dinges & Grace 1998（对 PVT lapse），结论：所评估指标中最可靠 | 【已验证】 |
| 0.15 / 0.25 / 0.40 三级阈值 | **未能追溯到一手出处** | **【不确定】** |
| 快速眨眼的闭眼时长截止值 | **< 250 ms / < 400 ms / < 500 ms 三种都在文献中被用过** | 【已验证】（综述转述一手文献编号） |

### 2.2 【已验证】Wierwille & Ellsworth 1994 —— PERCLOS 的起点

- **来源标题**：Evaluation of driver drowsiness by trained raters.
- **URL（题录）**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=AUTH%3A%22Wierwille%22%20AND%20TITLE%3A%22drowsiness%22&resultType=lite&format=json>
- **DOI**：<https://doi.org/10.1016/0001-4575(94)90019-1>
- **发表**：*Accident Analysis & Prevention* 1994;26(5):571–581
- **说明**：这是**PERCLOS 首次被确立的 1994 驾驶模拟器研究**（此归属由下面两份 FHWA/NHTSA 官方文件与 Abe 2023 综述**交叉确认**）。**我拿到的是题录，未读到全文数字。**

### 2.3 【已验证】Dinges & Grace 1998（FHWA-MCRT-98-006）—— 官方 Tech Brief

- **来源标题**：PERCLOS: A Valid Psychophysiological Measure of Alertness As Assessed by Psychomotor Vigilance
- **URL（官方落地页，**我实际打开了**）**：<https://rosap.ntl.bts.gov/view/dot/113>
- **PDF**：<https://rosap.ntl.bts.gov/view/dot/113/dot_113_DS1.pdf>（**PDF 仅 85.40 KB**，SHA-512 校验和见落地页）
- **DOI**：<https://doi.org/10.21949/1502740>
- **报告编号**：**FHWA-MCRT-98-006**；**日期：1998-10-01**；发布方：US DOT / FMCSA Technology Division
- **资助**：FHWA Office of Motor Carriers 部分资助，由 **NHTSA** 管理
- **摘要原文（关键句，逐字）**：
  - *"PERCLOS is the percentage of eyelid closure over the pupil over time and reflects **slow eyelid closures ("droops") rather than blinks**."*
  - *"**A PERCLOS drowsiness metric was established in a 1994 driving simulator study as the proportion of time in a minute that the eyes are at least 80 percent closed. (Wierwille et al., 1994)**"*
  - *"Of the drowsiness-detection measures and technologies evaluated in this study, the measure referred to as "PERCLOS" was found to be **the most reliable and valid** determination of a driver's alertness level."*
  - *"...FWHA and NHTSA consider PERCLOS to be among the most promising known real-time measures of alertness for in-vehicle drowsiness-detection systems."*
- **⚠️ 本项目极易踩的坑（来自上面第一句）**：**PERCLOS 的原始定义明确"反映慢闭眼（droop）而不是眨眼"**。VigiLens 若把**所有** EAR 低于阈值的帧都算进 PERCLOS，就会**把快速眨眼也计入**，与原始定义不一致。这个问题在 Abe 2023 中被列为"PERCLOS 定义不统一"的首要问题（见 2.5），**"是否排除快速眨眼"必须在 `docs/` 里显式声明**，否则跨研究/跨论文的数字不可比。

### 2.4 【已验证】Dinges, Mallis, Maislin & Powell 1998（DOT-HS-808-762）—— NHTSA 正式报告

- **来源标题**：Evaluation of techniques for ocular measurement as an index of fatigue and as the basis for alertness management
- **URL（官方落地页，**我实际打开了**）**：<https://rosap.ntl.bts.gov/view/dot/2518>
- **PDF**：<https://rosap.ntl.bts.gov/view/dot/2518/dot_2518_DS1.pdf>（**1.29 MB**）
- **报告编号**：**DOT-HS-808-762**；**日期：1998-04-01**；发布方：**NHTSA**
- **作者**：Dinges DF, Mallis MM, Maislin G, Powell JW
- **资助号**：DTNH22-93-D-07007
- **摘要原文（关键句）**：
  - *"This final report establishes the scientific validity of the ocular measure "Perclose" as a generally useful and reliable index of lapses in visual attention, i.e. the percentage of eyelid closure over the pupil."*
  - *"Perclose was previously specified as a relevant measure of drowsiness in several driving simulator studies (**NHTSA Final Report, DOT HS 808 640**)."* ← **注意这里还牵出第三份 NHTSA 报告 DOT HS 808 640**
  - 效度验证方式：受控**睡眠剥夺**研究，以 **PVT（Psychomotor Vigilance Task）** 作为视觉注意失误的标准
- **⚠️ 拼写坑**：该官方摘要把 PERCLOS 写作 **"Perclose"**（一份 1998 年文件的用词）。团队引用时建议保留 PERCLOS 并注明历史拼写。

### 2.5 【已验证】Abe 2023 综述 —— PERCLOS 的定义、争议与"老年人不敏感"

- **来源标题**：PERCLOS-based technologies for detecting drowsiness: current evidence and future directions
- **URL**：<https://pmc.ncbi.nlm.nih.gov/articles/PMC10108649/>
- **DOI**：<https://doi.org/10.1093/sleepadvances/zpad006>
- **发表**：*Sleep Advances* 2023;4(1):zpad006（Open Access，**CC BY 4.0**）；作者 Takashi Abe（筑波大学 WPI-IIIS）
- **定义（我逐字读到）**：
  - *"PERCLOS can be defined as the percentage of time that the eyes are more than 80% closed."*
  - *"the eye is defined as being closed when the eyelid is less than 20% open (0% is defined as completely closed)"*
  - **五种不同的闭眼判定口径都在文献中出现过**：① 0% 全闭 / 100% 全开；② 虹膜直径为 100% 的眼睑间距百分比；③ 瞳孔被眼睑遮挡的百分比；④ **瞳孔检测不到即判闭眼**；⑤ **EAR 低于某个确定值**（← 这一条正是 VigiLens 的做法，且被作者列为"简化做法"）
  - 也有人用 **眼开度 < 25%** 或 **< 30%** 作为闭眼定义
- **快速眨眼（fast eyelid closure）说明**：**< 250 ms**、**< 400 ms**、**< 500 ms** 三种截止值都被使用过
- **采样率**：既有研究用到 **2 / 3 / 6 / 10 / 24 / 60 / 120 Hz**
  - ⚠️ **对本项目的直接警告**：若采用"排除快速眨眼"的 PERCLOS 且截止值为 250–500 ms，则 **30 fps（= 33.3 ms/帧）足够**；但若论文用 120 Hz 口径，**30 fps 无法与之逐值对齐**。**跨研究比较 PERCLOS 前必须先对齐帧率与快速眨眼口径。**
- **局限性（摘要原文，含关键的年龄信息）**：
  - *"some cases have been reported wherein PERCLOS was **not** affected by drowsiness manipulations, such as in **moderate drowsiness conditions, in older adults, and during aviation-related tasks**."*
  - *"no single index is currently available as an optimal marker for detecting drowsiness during driving or other real-world situations."*
  - 建议方向之一是 **standardization to minimize differences in the definition of PERCLOS between studies**
- **对本项目的含义（最重要）**：**"老年人 PERCLOS 对困倦不敏感"是由一篇 2023 年综述明确记载的现象**，与 §3.2 的 Cai 2021 实测一致。**这直接说明：对老年用户使用统一的 PERCLOS 阈值会系统性漏报。**

### 2.6 【不确定】0.15 / 0.25 / 0.40 三级阈值的出处 —— **未能核实**

**我确实查了，但没能追溯到一手出处。** 记录事实如下：

| 我看到的说法 | 来源类型 | 置信度 |
|---|---|---|
| "典型推荐 PERCLOS 报警阈值为 **15%**" | Nature 期刊论文（检索摘要）<https://www.nature.com/articles/s41598-026-39195-y> | 【推测】 |
| 分级表：`0.075 < PERCLOS < 0.15` → Drowsy；`PERCLOS > 0.15` → Drowsy | PMC 论文分级表（检索摘要）<https://pmc.ncbi.nlm.nih.gov/articles/PMC9323611/> | 【推测】 |
| "recommended PERCLOS alarm threshold of 0.15 is used as the highest level of drowsiness" | ResearchGate 图表页（二手） | 【推测】 |
| 三级映射"as recommended by Trejo et al. (2007)" | arXiv 2209.04048 正文（二手转述） | 【推测】 |
| PERCLOS 阈值可设为 **10%**，假定临界点为 1 秒 microsleep | *Journal of Vision* VSS 2012 会议摘要 <https://jov.arvojournals.org/article.aspx?articleid=2141193> | 【推测】 |

**结论**：**"0.15 / 0.25 / 0.40"这组具体数字，我无法定位到任何一手文献**；能核实的只是"**0.15 被广泛当作推荐报警阈值**"这个二手共识，以及它**来自 Trejo et al. 2007 的转述**这一线索。
**团队若要在文档里写这组阈值，必须先自己打开 Trejo et al. (2007) 或 Grace et al. (1998, DASC) 原文确认，否则不要写。**
> 补充线索（未打开）：Grace R, et al. "A drowsy driver detection system for heavy vehicles." *Proceedings of the 17th DASC AIAA/IEEE/SAE Digital Avionics Systems Conference*, 1998。

### 2.7 本领域的检索式（可复现）

```
Dinges Grace 1998 PERCLOS valid psychophysiological measure of alertness psychomotor vigilance FHWA-MCRT-98-006
PERCLOS 0.15 0.25 0.40 threshold FHWA drowsiness alertness classification
Dinges Mallis Maislin Powell 1998 NHTSA DOT HS 808 762 ocular measurement index of fatigue alertness management
Wierwille Ellsworth 1994 PERCLOS P70 P80 EYEMEAS definition percentage of time eyelid closed 80 percent
"PERCLOS" threshold 0.15 origin drowsiness detection where does 0.15 come from
PERCLOS 0.4 alert 0.5 drowsy threshold heavy vehicle driver drowsiness detection system Grace 1998
Trejo 2007 PERCLOS drowsiness levels threshold 0.15 0.25 0.40 alert slightly drowsy
```

---

## 3. 年龄对困倦 / 疲劳易感性的影响

### 3.1 结论速查 —— 这一节是本笔记**最有价值**的部分

| 结论 | 数字 | 置信度 |
|---|---|---|
| **睡眠剥夺后，只有年轻组的眼部指标（眨眼时长、长闭眼次数、PERCLOS）显著升高；老年组不升高** | p < 0.05，仅 younger adults | **【已验证】** |
| 老年组仍有**驾驶**损害（车道偏离增加），但不是"睡着"造成的 | 3.5× 车道偏离，p = 0.008 | 【已验证】 |
| 年轻组睡眠剥夺后的驾驶损害**远大于**老年组 | 7.37× 车道偏离；11× 近碰撞风险 | 【已验证】 |
| 年轻 vs 老年（同时比较） | 年轻组 3.1× 车道偏离（p < 0.001）；近碰撞 79% vs 21%（p = 0.007） | 【已验证】 |
| 综述层面也记载"老年人 PERCLOS 不敏感" | 定性结论 | 【已验证】（Abe 2023） |

> **给团队的一句话**：**不能用一套 PERCLOS 阈值同时服务年轻人和老年人。** 这不再是"猜测的合理性"，而是有**实测数据 + 综述**双重支撑的**设计约束**。

### 3.2 【已验证】Cai et al. 2021 —— 公路实车 + 睡眠剥夺 + 年龄对照（核心证据）

- **来源标题**：On-road driving impairment following sleep deprivation differs according to age
- **URL**：<https://pmc.ncbi.nlm.nih.gov/articles/PMC8566466/>
- **DOI**：<https://doi.org/10.1038/s41598-021-99133-y>
- **发表**：*Scientific Reports* 2021;11:21561（**CC BY 4.0**，Open Access）
- **作者**：Cai AWT, Manousakis JE, Singh B, Kuo J, Jeppe KJ, Francis-Pester E, Shiferaw B, Beatty CJ, Rajaratnam SMW, Lenné MG, Howard ME, Anderson C（Monash University + Seeing Machines）
- **样本（表 1，逐字）**：
  - **年轻组 n = 16**：年龄 **24.26 ± 3.15 岁（范围 21–33）**，9 男 / 7 女，驾驶经验 5.59 ± 2.12 年
  - **老年组 n = 17**：年龄 **57.31 ± 5.17 岁（范围 50–65）**，9 男 / 8 女
  - 设计：封闭环道实车，每次 2 h 驾驶；条件 = (i) 前一晚 8 h 睡眠机会（well-rested）、(ii) **29 h 完全睡眠剥夺（TSD）**
- **结果（摘要逐字）**：
  - 两组在 TSD 后**主观困倦感**与**车道偏离**均增加（p < 0.05）
  - **年轻组**：车道偏离 **7.37×**；近碰撞事件风险 **11×**
  - **老年组**：车道偏离 **3.5×**（p = 0.008）；**近碰撞事件无显著增加（3/34 次驾驶）**
  - **年轻 vs 老年**：年轻组 **3.1×** 更多车道偏离（p < 0.001）；近碰撞事件 **79% vs 21%**（p = 0.007）
  - **★ 关键句（逐字）**：*"Ocular measures of drowsiness, including **blink duration, number of long eye closures and PERCLOS** increased following sleep loss **for younger adults only** (p < 0.05)."*
  - 作者结论：*"These results suggest that for older working-aged adults, driving impairments observed following sleep loss **may not be due to falling asleep**. Future work should examine whether this is attributed to other consequences of sleep loss, such as inattention or distraction from the road."*
- **引言中另外两个可引用数字**：
  - **18–24 岁驾驶员在夜间/清晨时段发生碰撞的可能性是其他时段的 14.2 倍**（该文引文 [10]）
  - 美国：困倦约贡献 **21% 的致命机动车碰撞**、**13% 的重伤碰撞**；澳大利亚：约 **20% 致命碰撞**、**30% 重伤碰撞**（该文引文 [4,5]）
- **对本项目的含义（三条可落地结论）**：
  1. **年龄分层参考带是有必要的** → §3.1 的结论可直接作为 `docs/` 里"为什么按年龄分带"的**唯一必需论据**。
  2. **老年组需要"非 PERCLOS 的补充指标"**：作者明确说老年组的损害"可能不是睡着导致的"，而是 inattention/distraction。**这不正好是 VigiLens 已经有的 `head pose` / `face visibility` / 行为指标吗？** —— 可以作为产品差异化的文献支撑。
  3. **反向风险**：老年组 PERCLOS 不敏感 ⇒ 若用统一阈值，**老年用户会被误判为"状态良好"**。这是**安全方向上的漏报（false negative）**，比误报更危险，必须进风险表。

### 3.3 【已验证】Abe 2023 —— 综述层面的相同结论
见 §2.5。原句：PERCLOS 在 *"in older adults"* 情况下**未受**困倦操纵影响。

### 3.4 【推测】其他线索（只读到检索摘要，**未打开一手页面**）

| 来源 | 我看到的说法 | 置信度 |
|---|---|---|
| *Safety* (MDPI) 2022;8(2):30 —— "Effects of Automation and Fatigue on Drivers from Various Age Groups" <https://www.mdpi.com/2313-576X/8/2/30> | 89 名驾驶员（45 女），研究自动化 × 疲劳 × 年龄分组 | 【推测】 |
| *Accident Analysis & Prevention*（ScienceDirect）—— "Effects of scheduled manual driving on drowsiness and response to..." <https://www.sciencedirect.com/science/article/abs/pii/S0001457519300661> | 驾驶模拟器研究，考察计划性人工驾驶对困倦与绩效的影响**及年龄差异** | 【推测】 |
| Cai 2021 引文 [11]–[13] 转述 | 实验室研究显示**老年人对睡眠剥夺更"耐受"**：持续注意任务绩效下降更小、慢眼动等生理困倦更少 | 【推测】 |
| Cai 2021 引文 [11] 转述 | **65–76 岁**老年人在睡眠剥夺下反应时更慢、PVT lapse 更多（相对其自身清醒状态） | 【推测】 |
| Cai 2021 引文 [20] 转述 | **52–74 岁**老年驾驶员在睡眠限制后模拟驾驶车道偏离显著增多、主观困倦更高（相对其自身清醒状态） | 【推测】 |

> **重要区分（防止团队误读）**：**"老年人对睡眠剥夺更耐受"** ≠ **"老年人不怕疲劳驾驶"**。上面的证据一致指向同一个精细结论：
> **老年人主观困倦与驾驶损害都会变差，但他们的"眼部困倦体征"不跟着变差。** 也就是说 **眼睛指标与主观/绩效的耦合关系本身随年龄改变了** —— 这是本项目必须处理的**老化偏置（age bias）**。

### 3.5 本领域的检索式（可复现）

```
older drivers PERCLOS blink duration age differences drowsiness driving simulator study
Åkerstedt Gillberg 1990 Karolinska Sleepiness Scale ... （见 §5）
```
- 直接命中的关键文献检索靠的是"older drivers + PERCLOS + blink duration + age differences"这一组关键词。
- **建议团队补充的检索式（本次因配额耗尽未做）**：
  - Europe PMC：`(TITLE:"drowsiness" OR TITLE:"sleepiness") AND (TITLE:"age" OR ABSTRACT:"older adults") AND (ABSTRACT:"PERCLOS" OR ABSTRACT:"blink duration")`
  - Europe PMC：`AUTH:"Duffy JF" AND TITLE:"age"` ；`AUTH:"Dijk DJ" AND TITLE:"sleepiness"`
  - Europe PMC：`AUTH:"Anund" AND TITLE:"drowsiness"` ；`AUTH:"Åkerstedt" AND AUTH:"Kecklund" AND TITLE:"age"`

---

## 4. 可作参考 / 基线用的公开数据集（**含"是否有年龄标注"的明确判定**）

> ⚠️ **本节是团队最关心的部分，也是本次调研**最不完整**的部分**：GitHub 全域不可达 + 后半段检索配额耗尽，导致部分数据集的**官方页面未能打开**。
> **凡是标【推测】的，团队若要用，必须自己打开官方页面再确认一次。**

### 4.1 年龄标注判定总表（**最重要的一张表**）

| 数据集 | **是否有受试者年龄标注？** | 依据 | 置信度 |
|---|---|---|---|
| **MRL Eye Dataset** | **❌ 没有**（只有 gender、glasses、eye state、reflections、lighting、sensor ID） | **官方页面逐条列出 8 个标注项，无 age** | **【已验证】** |
| **CEW** | **❌ 没有**（逐张图像的眼开/闭标签，无受试者人口学信息） | 官方页面 | **【已验证】** |
| **DMD** | **❓ 未能核实** | 官方页面未列元数据字段；GitHub 不可达 | **【不确定】** |
| **UTA-RLDD** | **❌ 很可能没有**（标注为 alert / low vigilant / drowsy 三级，未见人口学字段） | 官方 Google Sites 页**未能打开**（429 / fetch fail）；据 arXiv 论文与镜像描述 | **【推测】** |
| **NTHU-DDD** | **❌ 很可能没有** | 官方页面未打开；据 *Sensors* 2025 综述转述"36 subjects" | **【推测】** |
| **YawDD** | **❓ 不确定**（原文只说志愿者来自"different ages"，**但这不等于发布了年龄标签**） | ACM DL 摘要 | **【推测】** |
| **DROZY** | **❓ 不确定**（有 KSS、PSG，是否发布年龄字段未核实） | WACV 2016 论文 PDF 检索片段 | **【推测】** |
| **ZJU eyeblink** | **❌ 没有**（20 人 × 4 段视频，无人口学标注） | 二手描述 | **【推测】** |
| **AgeDB / MORPH-II / FG-NET / IMDB-WIKI / UTKFace / FairFace / Adience** | **✅ 有**（这是年龄估计的标准基准） | 见 §4.3 | 见 §4.3 |
| **MegaAge-Asian / AFAD** | **✅ 有，且是亚洲人脸** —— 对中文用户群最有价值 | 见 §8（**本次未能核实，列为待办**） | **【不确定】** |

> **一句话结论：本次调研覆盖的"疲劳/困倦"数据集里，没有一个被我确证带年龄标注。**
> **"年龄分层参考基线"不能靠现成疲劳数据集直接得到** —— 团队有两条路：
> ① **自采**（本项目自己的受试者，记录年龄分组）—— 最可控，但需伦理与隐私流程；
> ② **把"年龄估计基准集"（有年龄标签）与"疲劳数据集"（有疲劳标签）在方法层面拼起来**，而不是指望找到"两者都有"的现成数据集。
> 若一定要找"两者都有"的，最高价值线索是 **Drive&Act**（见 §8 待核实项）。

### 4.2 逐数据集详情

#### 4.2.1 【已验证】MRL Eye Dataset（Media Research Lab, VSB-TU Ostrava）

- **官方页面（我实际打开了）**：<https://mrl.cs.vsb.cz/eyedataset.html>
- **内容**：大规模**人眼图像**数据集；**红外（infrared）图像**，含低分辨率与高分辨率两档，涵盖多种光照、多种设备
- **规模**：**84,898 张图像**，**37 个人**（33 男 / 4 女）
- **标注字段（官方原文，**按文件名字段顺序**）**：
  1. subject ID（37 人）
  2. image ID（84,898 张）
  3. **gender**（0 = 男，1 = 女）
  4. glasses（0 = 无，1 = 有）
  5. eye state（0 = 闭，1 = 开）← **开/闭眼标签**
  6. reflections（0 = 无，1 = 小，2 = 大）
  7. lighting conditions（0 = 差，1 = 好）
  8. sensor ID（01 = RealSense，02 = IDS，03 = Aptina）
- **传感器与分辨率**：
  - **Intel RealSense RS 300**：**640 × 480**
  - **IDS Imaging**：**1280 × 1024**
  - **Aptina**：**752 × 480**
- **下载**：<http://mrl.cs.vsb.cz/data/eyedataset/mrlEyes_2018_01.zip>；瞳孔标注（约 **15,000** 个瞳孔点）：<http://mrl.cs.vsb.cz/data/eyedataset/pupil.txt>
- **许可**：**官方页面未声明 licence** —— **【不确定】**
- **引用**：论文 "Pupil localization using geodesic distance"（bibtex：<https://mrl.cs.vsb.cz/publications/fusek_isvc2018.bib>）；联系 Radovan Fusek
- **★ 年龄判定**：**没有年龄字段。** 官方把 8 个标注项**全部列出**了，其中**不含 age**。这是本次调研中**最干净的一条"否定结论"**。
  - ⚠️ **提醒**：网上（含 Kaggle 镜像、GTS.AI 页面）对该数据集的描述也**只提到 gender**，与官方一致。**不要相信任何声称 MRL 带年龄的说法。**
- **对本项目的价值**：**极高但用途不同** —— 它是"**眼开/闭 + 光照 + 反光**"的**质量门控与眨眼检测**训练/验证集（84,898 张、真实驾驶场景采集），**与 VigiLens 的 `light_score` / 眨眼状态判定高度对口**；但它**不能**用来做年龄分层。

#### 4.2.2 【已验证】CEW（Closed Eyes in the Wild）

- **官方页面（我实际打开了）**：<http://parnec.nuaa.edu.cn/_upload/tpl/02/db/731/template731/pages/xtan/ClosedEyeDatabases.html>（南京航空航天大学 NUAA，Xiaoyang Tan 组）
- **内容**：**2,423 名受试者**；其中
  - **1,192 名双眼闭合**（直接从互联网收集）
  - **1,231 名睁眼**（选自 **LFW** 数据库）
  - 裁脸统一缩放到 **100 × 100**，再取以定位眼位为中心的 **24 × 24** 眼图 patch
- **三种下载版本（Google Drive，**均为官方直链**）**：
  - 原始分辨率的带背景人脸：**20 MB rar**
  - 100 × 100 裁脸：**7.6 MB rar**
  - 24 × 24 眼图 patch：**2.6 MB rar**
- **许可（官方原文，逐字）**：*"The dataset is provided for **research purposes to a researcher only and not for any commercial use**. Please do not release the data or redistribute this link to anyone else without our permission."*
  - Copyright 2014, Xiaoyang Tan；联系 {x.tan, f.song}@nuaa.edu.cn
- **引用**：F. Song, X. Tan, X. Liu, S. Chen, *"Eyes Closeness Detection from Still Images with Multi-scale Histograms of Principal Oriented Gradients"*, **Pattern Recognition, 2014**
- **★ 年龄判定**：**没有年龄标注。** 它是**逐图像的眼开/闭**数据集，不含受试者人口学字段。
- **附带发现（官方页面同时列出）**：ZJU eyeblink database 的官方地址 —— <http://www.cs.zju.edu.cn/~gpan/database/db_blink.html>（该页面本身**我未能打开**）

#### 4.2.3 【已验证・部分】DMD（Driver Monitoring Dataset, Vicomtech）

- **官方页面（我实际打开了）**：<https://dmd.vicomtech.org/>
- **内容（官方描述）**：面向不同**驾驶员监控场景**的多模态数据集；含**真实驾驶**与**模拟器**两类场景（2 个 scenario）；覆盖 distraction、gaze allocation 等任务；含 depth 等模态
- **许可（官方原文，逐字）**：*"You must be 18 or older to download. **This dataset can only be used for academic purposes.** This dataset is published under the **Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 License**."*
  - **⚠️ 对本项目的直接影响**：**CC BY-NC-ND 4.0 = 非商业 + 禁止演绎**。用于竞赛（尤其涉及任何商业化表述）与**"禁止演绎"（ND）**两条都需要团队**逐字读许可证再决定**。赛制文档若要求可商用，**这条要写进合规风险表**。
- **引用（官方给出的 APA）**：Ortega, J. (2020). *DMD: A Large-Scale Multi-modal Driver Monitoring Dataset for Attention and Alertness Analysis.*（arXiv：<https://arxiv.org/abs/2008.12085>）
- **工具与代码**：TaTo、DEx；GitHub：<https://github.com/Vicomtech/DMD-Driver-Monitoring-Dataset>（**不可达**）
- **其他元信息**：Intel 曾是联盟成员与贡献方；联系 info-dmd@vicomtech.org
- **★ 年龄判定**：**未能核实。** 官方页面**没有列出元数据字段清单**，而列出字段的 GitHub README 不可达。
  - **待办（团队必做）**：打开 GitHub README / arXiv 2008.12085 确认是否有 subject 级 age 字段。

#### 4.2.4 【推测】UTA-RLDD（UTA Real-Life Drowsiness Dataset）

- **官方页面**：<https://sites.google.com/view/utarldd/home> —— **⚠️ 我未能打开**（Firecrawl 429 + local fetch fail）
- **内容（二手来源一致描述）**：**约 30 小时 RGB 视频**，**60 名健康受试者**，用个人手机或摄像头自采；视频段标注为 **alert / low vigilant / drowsy** 三级
- **论文**：Ghoddoosian R, Galib M, Athitsos V. *A Realistic Dataset and Baseline Temporal Model for Early Drowsiness Detection*, **arXiv:1904.07312**（CVPR Workshops 2019）<https://arxiv.org/pdf/1904.07312>
  - 该论文摘要（二手）称："a large and public real-life dataset of **60 subjects**, with video segments labeled as **alert, low vigilant, or drowsy**"
- **基线代码**：<https://github.com/rezaghoddoosian/Early-Drowsiness-Detection>（不可达）
- **★ 年龄判定**：**【推测】没有** —— 所有可见描述只提到三级困倦标注，未提及人口学字段。**但这不是确证，团队需自行打开论文/页面确认。**

#### 4.2.5 【推测】NTHU-DDD（NTHU Driver Drowsiness Detection）

- **官方页面**：**未能确认 URL，未能打开**
- **内容（二手转述）**：**36 名受试者**；台湾清华大学 CV Laboratory 采集；含**白天/夜间**、**戴/不戴眼镜**、**戴/不戴太阳镜**等条件下的分心与困倦场景
  - 来源：*Sensors* (MDPI) 2025 综述 <https://pmc.ncbi.nlm.nih.gov/articles/PMC11819803/>（检索摘要）；Kaggle 镜像 <https://www.kaggle.com/datasets/samymesbah/nthu-dataset-ddd-multi-class>
- **许可 / 年龄标注**：**【不确定】**（官方页面不可达 → 一切都未核实）

#### 4.2.6 【推测】DROZY（ULg Multimodality Drowsiness Database）

- **论文（可打开，但我被检索配额截断未读到正文）**：<https://orbi.uliege.be/bitstream/2268/191620/1/2016_WACV-copyrighted.pdf>（*WACV 2016*）
- **内容（检索片段）**：
  - **14 名健康受试者**到实验室，佩戴 **PSG（多导睡眠图）**
  - 模态：**KSS 主观困倦评分** + **PSG**（5 通道 EEG、2 通道 EOG、ECG 等）+ 视频
  - 综述转述为 **14 subjects（3 男 / 11 女）**（来源：arXiv 2408.12990 检索片段）
- **★ 年龄判定**：**【不确定】** —— 有 KSS 与 PSG，**但未见是否发布受试者年龄字段**。
- **对本项目的价值**：如果确实有 KSS + 视频，它是"**视频指标 ↔ KSS 主观金标准**"对齐的**理想参考集**（正好服务 §5 的量表锚定需求）。**强烈建议团队优先核实这个数据集。**

#### 4.2.7 【推测】YawDD（Yawning Detection Dataset）

- **官方/存档页面**：**IEEE DataPort（Open Access）**：<https://ieee-dataport.org/open-access/yawdd-yawning-detection-dataset>
- **论文**：<https://dl.acm.org/doi/pdf/10.1145/2557642.2563678>（ACM）
- **内容（ACM 摘要原文，检索片段）**：*"The dataset contains videos of **57 male and 50 female volunteers from different ages, ethnicities, and facial characteristics**."*
  - **⚠️ 关键区分**：原文说志愿者**来自不同年龄**（different ages），**但这只是描述采样多样性，并不等于数据集发布了年龄标签**。**不要据此认为 YawDD 有年龄标注。**
- **★ 年龄判定**：**【不确定】** —— 未见发布年龄字段的证据。
- **对本项目的价值**：**打哈欠（MAR / yawn count）**的直接对口数据集。

#### 4.2.8 【推测】ZJU eyeblink database

- **官方 URL（由 CEW 官方页面列出，可信）**：<http://www.cs.zju.edu.cn/~gpan/database/db_blink.html> —— **我未能打开**
- **内容（二手，两处一致）**：
  - **80 段短视频**；**20 名受试者 × 4 段**（正面戴/不戴眼镜等变化）
  - 分辨率 **320 × 240 @ 30 FPS**
  - 来源：*Expert Systems with Applications* 2021（ScienceDirect 检索片段）；blinkingmatters.com
- **★ 年龄判定**：**【推测】没有。**
- **对本项目的价值**：**眨眼检测的经典小基准**（80 段、20 人），适合做算法回归测试；分辨率 320×240 与 VigiLens 的降规格链路接近。

### 4.3 【已验证】带年龄标注的人脸数据集（年龄估计的标准基准）

> 这些是"**有年龄标签**"的那一类，用来做**年龄推断模型的训练/评测**，以及（间接地）为分层提供年龄定义。

**来自 arXiv 2511.14689（Jamo 2025）Table I，我打开了该页面并读到该表**：

| 数据集 | 图像数 | 分辨率 / 质量 | 年龄范围 | 备注（同文原文） |
|---|---|---|---|---|
| **UTKFace** | **20,000+** | **~200×200 px（低）** | **0–116** | 标注 age + gender + ethnicity；分布较均衡 |
| **FairFace** | **100,000+** | ~300×300 px（中） | **类别式（0–2 到 70+）** | 跨族裔/性别均衡；**年龄是宽类别而非精确值**（限制回归用途） |
| **IMDB-WIKI** | **500,000+** | 常 > 400 px（混合） | **0–100+** | 最大公开年龄数据集；**年龄分布严重不均衡（15–35 岁过代表）**；标签来自照片拍摄日期估计 ⇒ **有噪声**；不含年龄标签的图像也有 |
| **IMDB-Clean** | IMDB-WIKI 的清洗子集 | **最高 1024 px（高）** | **0–100+** | 去掉空白图/非人脸/错误年龄标签；标注更一致 |
| **VGGFace2** | **3.31 M** | 高分辨率网络图（混合） | **无年龄标签** | 9,131 个身份；用于训练特征提取器，**不提供显式年龄标签** |

**来自 Adience 官方页面（我打开了 <https://talhassner.github.io/home/projects/Adience/Adience-data.html>）**：

- **Adience（unfiltered faces for gender and age classification）**
  - **总照片数：26,580**；**总受试者数：2,284**
  - **年龄组 / 标签数：8 —— 官方写作 `(0-2, 4-6, 8-13, 15-20, 25-32, 38-43, 48-53, 60-)`**
  - **Gender labels：Yes；In the wild：Yes；Subject labels：Yes**
  - 来源：Flickr 相册，由 iPhone5 或更新机型自动上传，作者以 **Creative Commons (CC)** 许可公开发布
  - 文件：`faces.tar.gz (936M)`、`aligned.tar.gz (1.9G)`、`fold_0..4_data.txt`（五折交叉验证索引，含标签）、`fold_frontal_0..4_data.txt`（仅近似正面）
  - **获取方式**：需在页面上**提交姓名 + 邮箱**才能看到 FTP 用户名/密码 ⇒ **不是完全匿名直下**；服务器 `agas.openu.ac.il`，备用 <http://www.cslab.openu.ac.il/download/>
  - **许可**：页面提供 `LICENSE.txt` 链接（<https://talhassner.github.io/home/projects/Adience/LICENSE.txt>），**但我未打开** ⇒ **【不确定】**
  - 引用：Eran Eidinger, Roee Enbar, Tal Hassner, *Age and Gender Estimation of Unfiltered Faces*
- **⚠️ 一处必须记录的标签不一致（会直接影响模型输出解释）**：
  - **Adience 官方**写 **`8-13`** 与 **`60-`**
  - **OpenCV/Levi-Hassner 的 Caffe 模型输出**写 **`8-12`** 与 **`60-100`**（见 §6.1、§6.2）
  - **两处不一致**。团队在写"年龄带定义"时**必须选定一个口径并注明出处**，否则和模型输出对不上。

### 4.4 本领域的检索式（可复现）

```
NTHU-DDD driver drowsiness detection dataset subjects age annotation download licence
UTA-RLDD real-life drowsiness dataset 60 subjects age gender DROZY YawDD MRL eye dataset age labels
NTHU DDD drowsiness dataset download official 36 subjects
MRL eye dataset age gender glasses annotations filename
UTA Real-Life Drowsiness Dataset UTA-RLDD 60 subjects official download
DROZY database University Liege drowsiness 14 subjects age gender PSG KSS licence
YawDD yawning detection dataset NRC Canada 107 videos subjects age
CEW closed eyes in the wild dataset 2423 subjects official page
ZJU eyeblink dataset 80 subjects official download
DMD Driver Monitoring Dataset age gender subject metadata licence Politecnico Torino
```

---

## 5. 经过验证的疲劳 / 困倦量表（可用于自评金标准锚定）

### 5.1 四量表对照总表

| 量表 | 全称 | 条目数 / 量程 | 题录 | 置信度 |
|---|---|---|---|---|
| **KSS** | Karolinska Sleepiness Scale | **单条目，9 点**（1–9）；有 **10 点**修订版 | Åkerstedt T, Gillberg M, *Int J Neurosci* **1990**;52:29–37 | **【已验证】**（含锚点全文） |
| **SSS** | Stanford Sleepiness Scale | 单条目，**7 点**（**条目数与锚点未核实**） | Hoddes E, Zarcone V, Smythe H, Phillips R, Dement WC, *Psychophysiology* **1973**;10(4):431–436 | **【已验证】**（题录）；锚点【不确定】 |
| **CFS** | Chalder Fatigue Scale | 原始 **14 条目**；另有**修订 11 条目**版（**未核实**） | Chalder T, Berelowitz G, Pawlikowska T, Watts L, Wessely S, Wright D, Wallace EP, *J Psychosom Res* **1993**;37(2):147–153 | **【已验证】**（题录）；条目数细节【推测】 |
| **FSS** | Fatigue Severity Scale | **9 条目，1–7 分**（**锚点未核实**） | Krupp LB, LaRocca NG, Muir-Nash J, Steinberg AD, *Arch Neurol* **1989**;46(10):1121–1123 | **【已验证】**（题录）；锚点【推测】 |

### 5.2 【已验证】KSS —— 锚点全文（**本笔记中"锚点"信息最完整的一个量表**）

- **来源**：*STOP, THAT and One Hundred Other Sleep Scales* 一书的 KSS 章节 PDF（宾夕法尼亚大学 CBTI 站点）
- **URL（我实际打开了）**：<https://www.med.upenn.edu/cbti/assets/user-content/documents/Karolinska%20Sleepiness%20Scale%20(KSS)%20Chapter.pdf>
- **量表本体（官方 PDF 逐字）**：

| 分 | 标签 |
|---|---|
| 1 | Extremely alert |
| 2 | Very alert |
| 3 | Alert |
| 4 | Rather alert |
| 5 | Neither alert nor sleepy |
| 6 | Some signs of sleepiness |
| 7 | Sleepy, but no effort to keep awake |
| 8 | Sleepy, but some effort to keep awake |
| 9 | **Very sleepy, great effort to keep awake, fighting sleep** |
| （10） | **Extremely sleepy, can't keep awake** ← **修订版新增项** |

- **计分说明（PDF 逐字）**：*"This is a **9-point scale** (1 = extremely alert, 3 = alert, 5 = neither alert nor sleepy, 7 = sleepy – but no difficulty remaining awake, and 9 = extremely sleepy – fighting sleep). There is a **modified KSS** that contains one other item: **10 = extremely sleepy, falls asleep all the time**."*
- **时间窗**：受试者报告**过去 10 分钟**的心理生理状态（*"in the last 10 min"*）
- **用途**：测量 **situational（状态性）sleepiness**，对波动敏感；**不是 trait 量表**，故不常用于临床
- **信效度（PDF 转述）**：Kaida et al. 发现 KSS 与 EEG 及行为变量高度相关，效度高；但因分数随先前睡眠、时段等变化，**test–retest 信度难以推断**
- **规范引用**：**Akerstedt T, Gillberg M. (1990). Subjective and objective sleepiness in the active individual. *International Journal of Neuroscience*, 52, 29–37.**
- **⚠️ 版权限制（必须写进团队文档）**：
  - 版权页原文：*"Copyright © Torbjörn Åkerstedt, 1990. **Reproduction, duplication or adaptation without the written consent of Torbjörn Åkerstedt is strictly prohibited.**"*
  - 获取方式：*"A copy can be obtained from the authors."* 联系人：Torbjörn Åkerstedt, IPM & Karolinska Institutet, Box 230, 17177 Stockholm, Sweden
  - **⇒ 团队若要在产品里内置 KSS 让用户打分，必须考虑这一条授权要求。** 这与 `AGENTS.md` 的"记录必交 / 隐私红线"同级的**合规风险**。
- **对本项目的价值**：**KSS 是"自评金标准"的首选** —— 单条目、9 点、10 分钟窗口，**用户负担最低**，且是 DROZY 数据集实际使用的量表（§4.2.6），可与视频指标直接对齐。

### 5.3 【已验证・题录】Stanford Sleepiness Scale（SSS）

- **规范引用**：**Hoddes E, Zarcone V, Smythe H, Phillips R, Dement WC. Quantification of sleepiness: a new approach. *Psychophysiology*. 1973;10(4):431–436.**
- **DOI**：<https://doi.org/10.1111/j.1469-8986.1973.tb00801.x>
- **题录入口（我实际读到的 API 记录）**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=TITLE%3A%22Quantification%20of%20sleepiness%22&resultType=lite&format=json>
- **被引**：1,550 次（Europe PMC 计数）
- **⚠️ 未核实**：**7 点量程与 7 个锚点的具体措辞我未读到一手页面** ⇒ 标【不确定】。二手说法（Wikipedia 等）称其为 7 点自评，**但按本项目规矩，不要引用未核实的锚点**。
- **待办**：打开原论文或权威量表汇编（如同一本 *STOP, THAT…*）确认锚点。

### 5.4 【已验证・题录】Chalder Fatigue Scale（CFS）

- **规范引用**：**Chalder T, Berelowitz G, Pawlikowska T, Watts L, Wessely S, Wright D, Wallace EP. Development of a fatigue scale. *Journal of Psychosomatic Research*. 1993;37(2):147–153.**
- **DOI**：<https://doi.org/10.1016/0022-3999(93)90081-p>
- **题录入口**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=AUTH%3A%22Chalder%20T%22%20AND%20TITLE%3A%22Development%20of%20a%20fatigue%20scale%22&resultType=lite&format=json>
- **被引**：**1,997 次**（Europe PMC 计数）
- **【推测】版本细节**：检索到 *"Psychometric properties of the Chalder Fatigue Scale revisited"*（<https://pmc.ncbi.nlm.nih.gov/articles/PMC4529874/>）的片段称其为 **"The 11-item Chalder Fatigue Scale (CFS)"** ⇒ **说明存在 14 条目原始版与 11 条目修订版两种口径**。
  - ⚠️ **团队必须选定版本并注明**，否则与其他研究不可比。
- **用途定位**：**特质性/长期疲劳（chronic fatigue）**，不是"当前 10 分钟有多困"。**不适合做逐分钟对齐的金标准**，适合做"受试者基线疲劳水平"的分层变量。

### 5.5 【已验证・题录】Fatigue Severity Scale（FSS）

- **规范引用**：**Krupp LB, LaRocca NG, Muir-Nash J, Steinberg AD. The fatigue severity scale. Application to patients with multiple sclerosis and systemic lupus erythematosus. *Archives of Neurology*. 1989;46(10):1121–1123.**
- **DOI**：<https://doi.org/10.1001/archneur.1989.00520460115022>
- **题录入口**：<https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=AUTH%3A%22Krupp%20LB%22%20AND%20TITLE%3A%22fatigue%20severity%20scale%22&resultType=lite&format=json>
- **被引**：**4,459 次**（Europe PMC 计数）
- **【推测】条目与量程**：宾大 CBTI 站点的 FSS PDF（<https://www.med.upenn.edu/cbti/assets/user-content/documents/Fatigue%20Severity%20Scale%20(FSS).pdf>）在检索片段中称 *"The FSS is a **nine-item** instrument designed to assess fatigue as a symptom of a variety of different chronic conditions and disorders."* —— **9 条目**这一条来自检索片段；**1–7 分的锚点措辞我未核实**。
- **用途定位**：与 CFS 类似，**特质性疲劳**，做基线分层而非逐时对齐。

### 5.6 给团队的量表选型建议（含依据）

| 目的 | 推荐量表 | 理由 |
|---|---|---|
| **逐次测量 / 与视频指标逐时对齐** | **KSS** | 单条目、9 点、**10 分钟窗口**，负担最低；DROZY 已用（§4.2.6） |
| 需要"标准实验心理学"传统锚点 | SSS | 1973 年经典，但**锚点待核实** |
| 受试者**基线**疲劳水平分层 | CFS 或 FSS | 特质性、多条目，适合做协变量 |
| ⚠️ **不建议** | 把 CFS/FSS 当"当前困倦度" | 它们测的是**长期疲劳症状**，与 PERCLOS 的时间尺度不匹配 |

### 5.7 本领域的检索式（可复现）

```
Åkerstedt Gillberg 1990 Karolinska Sleepiness Scale 9 point anchors extremely alert very sleepy fighting sleep
Chalder 1993 development of a fatigue scale 14 items Krupp 1989 fatigue severity scale 9 items anchors
```
- Europe PMC API：`TITLE:"Quantification of sleepiness" OR (AUTH:"Chalder T" AND TITLE:"Development of a fatigue scale")`
- Europe PMC API：`AUTH:"Krupp LB" AND TITLE:"fatigue severity scale"`

---

## 6. 单张 RGB 人脸图像的年龄估计（**离线、ARM CPU、Zynq PS 侧可跑**）

### 6.1 模型总表（**每一条都标注了"我实际读到了什么"**）

| 方案 | 输出格式 | 输入 | 模型体积 | 许可 | 权重可下载？ | 置信度 |
|---|---|---|---|---|---|---|
| **Levi & Hassner Caffe（OpenCV 用的 age_net）** | **8 段分类** | **227×227** | **未核实** | **未核实** | **✅ 官方 Google Drive 直链（作者页给出）** | 【已验证】（除体积/许可） |
| **SSR-Net** | **回归（连续年龄）** | 人脸图（论文未给固定分辨率细节） | **0.32 MB** | **未核实** | 代码 repo 不可达 ⇒ **未核实** | 【已验证・体积】 |
| **MiVOLO** | 回归（年龄）+ 性别 | **双输入：人脸 + 人体** | **未核实** | **未核实** | 论文称"models + code publicly released" | 【已验证・摘要】 |
| **DeepFace** | 回归（年龄） | **224×224**（VGG-Face）或 **112×112**（ArcFace） | VGG-Face 主干 **~134 M 参数** | **MIT（代码）** | 随包自动下载 | 【推测】 |
| **InsightFace（genderage head）** | 年龄 + 性别 | **112×112**（ArcFace 主干） | **buffalo_s ≈ 159 MB**（整包） | 代码 **MIT**；**权重"non-commercial research only"** | 随包自动下载 | 【推测】 |
| **DEX** | 回归（期望值） | 人脸图 | **未核实** | **未核实** | **未核实** | **【不确定】**（完全未核实） |
| **onnx-community/age-gender-prediction-ONNX** | 回归（年龄）+ 性别 | 未核实 | 未核实 | 未核实 | **HuggingFace 可下载** | 【推测】 |
| **MobileAgeNet** | **连续回归（bounded regression）** | 未核实 | 轻量（面向移动端） | 未核实 | arXiv PDF 存在 | 【推测】 |

> **⚠️ 一条对 Zynq PS 侧极其重要的横切结论**：
> **DeepFace / InsightFace 这类"现成 Python 框架"在 Zynq-7020 的 PS（双核 Cortex-A9 @ ~667 MHz–1 GHz）上基本不可行** —— 因为主干是 **VGG-Face（~134 M 参数）** 或 **ArcFace（~34 M 参数）**，输入 112–224²，属于"重 CNN"。它们**适合当 PC 侧参考/离线标注工具**，不适合上板。
> **真正适合 ARM CPU 的候选是 SSR-Net（0.32 MB）与 Levi & Hassner 的极简 Caffe 网（3 层卷积 + 2 层 512 FC）** —— 后者在论文中报告单图推理**约 200 ms**（在作者的 GPU 机器上，**不是 CPU 数字**，不能直接套到 Zynq）。

### 6.2 【已验证】Levi & Hassner 2015 —— OpenCV 那个 Caffe 年龄模型的原始出处

- **来源标题**：Age and Gender Classification using Convolutional Neural Networks
- **官方项目页（我实际打开了）**：<https://talhassner.github.io/home/publication/2015_CVPR>
- **论文 PDF（我实际打开了）**：<https://talhassner.github.io/home/projects/cnn_agegender/CVPR2015_CNN_AgeGenderEstimation.pdf>
- **发表**：**CVPR Workshops 2015**；作者 Gil Levi, Tal Hassner（The Open University of Israel）
- **★ 官方权重下载（作者页逐字给出的直链）**：
  - **age classification Caffe model**：<https://drive.google.com/open?id=1kiusFljZc9QfcIYdU2s7xrtWHTraHwmW>
  - **age deploy prototext**：<https://drive.google.com/open?id=1kWv0AjxGSN0g31OeJa02eBGM0R_jcjIl>
  - gender model / prototext、mean image 亦有独立链接
  - 另有 **TensorFlow 第三方复现**：<https://github.com/dpressel/rude-carnie>（Daniel Pressel，2016-11-21 加入）
- **官方页面原文关于下载的声明**：*"We provide the convolutional neural network models for age and gender classification used in the paper."* —— **⇒ 权重确实是官方公开可下载的（Google Drive 直链，不需要 GitHub）**。这一点对本项目**很关键**，因为 GitHub 在本机不可达而 **Google Drive 链接来自作者页面**。
- **架构（论文逐字读到）**：
  - **3 个卷积层**：96×(3×7×7) → 256×(96×5×5) → 384×(256×3×3)，每层后接 ReLU + 池化（前两层还有 LRN 局部响应归一化）
  - **2 个全连接层，各 512 神经元**，后接 ReLU + dropout（**dropout ratio 0.5**）
  - 最后接 softmax 输出**年龄 8 类**或性别 2 类
  - 输入：**先缩放到 256×256，再裁剪 227×227 送入网络**
  - **训练：完全从零开始，不使用任何预训练权重**（*"we do not use pre-trained models for initializing the network; the network is trained, from scratch"*），仅用 Adience 的数据与标签
  - 数据增强：随机 227×227 裁剪 + 随机水平镜像
  - 优化：SGD，**batch size 50**，初始学习率 **e⁻³，10K 迭代后降为 e⁻⁴**
  - 预测两种方式：**Center Crop**（单张 227×227）与 **Over-sampling**（从 256×256 取四角 + 中心共 5 个 227×227 裁剪，连同各自水平镜像，共 10 个视图，取平均）
  - **推理速度（论文逐字）**：*"predicting age or gender on a single image using our network requires about **200ms**"*；训练在 **Amazon GPU 机器（1,536 CUDA cores, 4 GB 显存）**上约 4 小时
- **★ 年龄分档（**注意与 Adience 官方页面的用词差异**）**：论文正文说 Adience 年龄分类需区分 **eight classes**；
  - **PyImageSearch 教程（见 §6.3）给出的模型输出档位是**：`(0-2), (4-6), (8-12), (15-20), (25-32), (38-43), (48-53), (60-100)`
  - **Adience 官方页面写的是**：`(0-2, 4-6, 8-13, 15-20, 25-32, 38-43, 48-53, 60-)`
  - **⇒ 两处不一致（`8-12` vs `8-13`，`60-100` vs `60-`）。** 我不想用没读到的数字去填，**故此处如实并列两套口径**。
- **⚠️ 未能核实**：
  - **`age_net.caffemodel` / `age_deploy.prototxt` 的磁盘体积**（团队常引用的"约 45 MB"我**没有**任何一手依据 ⇒ **不要写**）
  - **模型权重的 licence**：作者页只写 *"Copyright 2015, Gil Levi and Tal Hassner"*，**未给许可证条款** ⇒ **【不确定】**
  - **Adience 上的精确准确率**：文章正文我读到 4.1 节"Adience benchmark consists of roughly 26K images of 2,284 subjects"，但**在 "Table 1 lists the breakdown…" 处被截断，我没有读到结果表** ⇒ **准确率数字【未能核实】，不要引用任何"50.7% / 84.7%"之类的说法**（那些数字我一次都没读到）

### 6.3 【推测】OpenCV 侧的打包细节（PyImageSearch 教程）

- **来源**：*OpenCV Age Detection with Deep Learning*，Adrian Rosebrock，2020-04-13，<https://pyimagesearch.com/2020/04/13/opencv-age-detection-with-deep-learning/>
- **来源性质**：**知名教程博客（二手）**，不是一手论文 ⇒ 其细节标【推测】
- **文件命名（教程给出的项目结构逐字）**：
  ```
  age_detector/age_deploy.prototxt
  age_detector/age_net.caffemodel
  face_detector/deploy.prototxt
  face_detector/res10_300x300_ssd_iter_140000.caffemodel
  ```
  - **⚠️ 注意命名差异**：教程用的是 **`age_deploy.prototxt`**，而任务描述里写的是 **`deploy_age.prototxt`**。**两个名字都出现过**（Levi & Hassner 作者页称 "deploy prototext"，未给精确文件名）⇒ **团队以自己下到的压缩包内实际文件名为准，不要照抄任何一个**。
- **8 个年龄档（教程代码里的字面量，逐字）**：
  ```python
  AGE_BUCKETS = ["(0-2)", "(4-6)", "(8-12)", "(15-20)", "(25-32)",
                 "(38-43)", "(48-53)", "(60-100)"]
  ```
- **预处理（教程代码逐字）**：
  - 人脸检测 blob：`blobFromImage(image, 1.0, (300, 300), (104.0, 177.0, 123.0))`
  - 年龄输入 blob：`blobFromImage(face, 1.0, (227, 227), (78.4263377603, 87.7689143744, 114.895847746), swapRB=False)`
  - **⇒ 这组均值（78.43, 87.77, 114.90）是可直接抄进实现的常数**，省去自己反推。
- **输出解释**：`preds = ageNet.forward(); i = preds[0].argmax(); age = AGE_BUCKETS[i]; ageConfidence = preds[0][i]` —— **取 argmax 的那个档 + 其置信度**。示例输出 `(25-32): 57.51%`。
- **⚠️ 许可注意**：该教程把模型放在**自己的下载包**里且**需要邮箱订阅**。**对 VigiLens 更干净的做法是走 §6.2 的官方 Google Drive 直链**，而不是从教程博客下载（来源可追溯性更好）。

### 6.4 【已验证】SSR-Net —— **目前最适合 ARM/嵌入式的候选**（0.32 MB）

- **来源标题**：SSR-Net: A Compact Soft Stagewise Regression Network for Age Estimation
- **PDF（我实际打开了）**：<https://www.csie.ntu.edu.tw/~cyy/publications/papers/Yang2018SSR.pdf>
- **发表**：**IJCAI 2018**；作者 Tsun-Yi Yang, Yi-Hsuan Huang, Yen-Yu Lin, Pi-Cheng Hsiu, Yung-Yu Chuang（Academia Sinica / National Taiwan University）
- **★ 模型体积（摘要逐字）**：*"The resultant SSR-Net model is very compact and takes only **0.32 MB**. Despite its compact size, SSR-Net's performance approaches those of the state-of-the-art methods whose model sizes are often **more than 1500 larger**."*
- **可对比的体积锚点（论文逐字）**：
  - **ORCNN**（Niu et al. 2016）：约 **1.7 MB**
  - 多数 SOTA CNN 年龄估计模型：**> 500 MB**
- **★ 输出格式**：**回归（连续年龄）**，但**实现方式是"多阶段分类 + 求期望值"**：
  - 灵感来自 **DEX**：把年龄估计当作多分类，再把分类结果通过**求期望**转成回归
  - **coarse-to-fine 多阶段**：第 k 阶段只负责细化第 k−1 阶段的判定（例如"偏年轻 / 差不多 / 偏老"），因此每阶段类别少、神经元少 ⇒ 模型小
  - **dynamic range（动态区间）**：每个年龄组的区间可以按输入人脸**平移（shift）与缩放（scale）**，缓解分类带来的量化误差
- **输入**：单张人脸图像（论文未在摘要/方法前半段给出固定的输入分辨率细节；**我在被截断前未读到具体输入尺寸** ⇒ **未核实**）
- **训练损失**：**简单回归损失（MAE）**，端到端，**无需**序数信息或分布/秩相似度等额外监督
- **代码**：论文脚注给出 `https://github.com/shamangary/SSR-Net`（**GitHub 不可达 ⇒ 未核实**）
- **⚠️ 明确未核实**：
  - **MORPH-II 上的 MAE**：我**没有读到结果表**（PDF 抓取在架构小节被截断）⇒ **不要写任何 MORPH-II 数字**
  - **权重是否真的可下载、许可为何**：GitHub 不可达 ⇒ **未核实**
- **对本项目的含义**：**0.32 MB 是本笔记中唯一一个有一手论文数字支撑的"可在 MCU/A9 上跑的年龄模型体积"**。若团队要做 PS 侧年龄推断，**SSR-Net 应是第一优先评估对象**，但必须**先解决两个未知：权重可获取性 + 许可**。

### 6.5 【已验证・摘要】MiVOLO —— 精度最高但**依赖人体信息**，不适合只看脸的终端

- **来源标题**：MiVOLO: Multi-input Transformer for Age and Gender Estimation
- **arXiv 摘要页（我实际打开了）**：<https://arxiv.org/abs/2307.04616>
- **DOI**：<https://doi.org/10.48550/arXiv.2307.04616>
- **版本**：v1 2023-07-10；**v2 2023-09-22**
- **作者**：Maksim Kuprashevich, Irina Tolstykh
- **★ 关键机制（摘要逐字）**：*"Our method integrates both tasks into a unified **dual input/output model**, leveraging not only **facial information but also person image data**. This improves the generalization ability of our model and **enables it to deliver satisfactory results even when the face is not visible in the image**."*
- **宣称**：在**四个主流基准**上达到 SOTA；具备**实时处理能力**；提出一个基于 **Open Images Dataset** 的新基准（人工标注者多投票聚合，精度高）；并声称在**多数年龄区间上显著优于人类**（*"significantly outperforms humans across a majority of age ranges"*）
- **权重与代码**：*"we grant public access to our models, along with the code for validation and inference. In addition, we provide extra annotations for used datasets and introduce our new benchmark."*
  - 论文给出的项目仓库：<https://github.com/WildChlamydia/MiVOLO>（**GitHub 不可达 ⇒ 未核实**）
- **⚠️ 明确未核实**：**MORPH-II MAE、模型体积、权重许可** —— 全部**未核实** ⇒ **不要写数字**
- **对本项目的含义（很重要的一条产品判断）**：
  - MiVOLO 的**精度优势来自"人体 + 人脸双输入"**。**VigiLens 是"一个普通 RGB 摄像头、以脸部 ROI 为主"的桌面终端** —— 若只有肩部以上画面，MiVOLO **退化为人脸单输入**，其宣称的精度不再适用。
  - **⇒ 不要因为"MiVOLO 是 SOTA"就直接选它**；它的核心卖点在 VigiLens 的场景里可能用不上。
  - 但它有条**有趣的旁支价值**：*"即使人脸不可见也能给出满意结果"* —— 这与 VigiLens 的 `face.visible` / `face_visibility_ratio` 质量门控**逻辑上是冲突的**（VigiLens 的承诺是"看不见脸就说不可靠"，而不是"猜一个年龄"）。**这可以写成一段产品定位对比。**

### 6.6 【推测】DeepFace / InsightFace —— **许可与算力双重风险**

- **主要依据（我打开了这个页面）**：Jamo S. *Impact of Image Resolution on Age Estimation with DeepFace and InsightFace*, **arXiv:2511.14689v1** [cs.CV], 2025-11-18, **CC BY 4.0** —— <https://arxiv.org/html/2511.14689v1>
- **★ 该论文给出的独立实测（**带明确基准**）**：
  - **基准**：**1000 张来自 IMDB-Clean 的图像 × 7 种分辨率 = 7000 个测试样本**
  - **最佳分辨率 224×224 处**：**MAE 10.83 岁（DeepFace）** / **7.46 岁（InsightFace）**
  - **分辨率影响显著**：偏离最优分辨率后 MAE 明显上升；**过低与过高分辨率都会使精度下降**
  - **InsightFace 在所有分辨率下都比 DeepFace 快**
  - 版本：DeepFace **0.0.95**，InsightFace **0.7.3**
- **该论文 Table II 摘要（逐字转述）**：

  | 属性 | DeepFace | InsightFace |
  |---|---|---|
  | 开源许可 | **MIT License** | **MIT License** |
  | 主干 | VGG-Face, Facenet, OpenFace, DeepID, ArcFace, Dlib | ArcFace, Partial FC, SubCenter ArcFace |
  | 检测模型 | OpenCV, SSD, Dlib, MTCNN, RetinaFace | RetinaFace, SCRFD |
  | 输入 | **224×224**（VGG-Face）/ **112×112**（ArcFace） | **112×112**（ArcFace） |
  | 更新 | 活跃（2025, v0.0.95） | 活跃（2025, v0.7.3） |

- **该论文 Table III 架构参数（逐字）**：VGG-Face **224×224×3**、4096 维嵌入、**~134 M 参数**、36 层；ArcFace **112×112×3**、512 维嵌入、**~34 M 参数**、162 层
- **该论文转述的 DeepFace 官方宣称**：*"According to the official documentation, DeepFace reports an average error of approximately **±4.65 years (MAE)** on **public test data**"*
  - ⚠️ **注意**：这是**转述**，且**"public test data"没有指明是哪个数据集** ⇒ 标【推测】，**不可作为基准数字使用**（一个没有基准的 MAE 等于没有意义 —— 正是任务要求的"不要给没有基准的准确率"）。**对比之下，7.46 / 10.83 那两个数字有明确基准（IMDB-Clean, 224×224）**，这才是可引用的形式。
- **★ InsightFace 权重的许可风险（**独立来源，与上表"MIT"冲突**）**：
  - **LocalAI 官方文档**（<https://localai.io/docs/features/face-recognition/index.html>）的许可警告表逐字：
    - `face-detect-buffalo-l`（SCRFD-10GF + ArcFace R50 + **GenderAge**）→ **"Non-commercial research only (upstream insightface weights)"**
    - `face-detect-buffalo-s`（SCRFD-500MF + MBF + **GenderAge**）→ **"Non-commercial research only"**
    - `insightface-buffalo-s`（SCRFD-500MF + MBF + **GenderAge**）→ **~159 MB**，**"Non-commercial research only"**
  - **InsightFace 仓库 README 片段**（经检索抽取）：*"The code of InsightFace is released under the **MIT License**. There is no limitation for both academic and commercial usage. [models] are available for **non-commercial** ..."*
  - **⇒ 正确理解是：InsightFace 的 `*_s`/`*_l` 模型包里自带的 `genderage.onnx` 是"非商业研究用途"**，MIT 只覆盖**代码**。
  - **⇒ 对竞赛项目的影响**：若赛制或后续商业化要求允许商用，**InsightFace 的 genderage 权重不可用**。**这是一条必须写进合规风险表的结论。**
  - ⚠️ 置信度：以上两条都**不是**我打开 GitHub 读到的（GitHub 不可达），而是 LocalAI 文档（**我未打开一手页面，来自检索片段**）+ 检索抽取的 README 文本 ⇒ **【推测】**
- **【推测】其他候选线索（仅检索片段）**：
  - **HuggingFace `onnx-community/age-gender-prediction-ONNX`**：<https://huggingface.co/onnx-community/age-gender-prediction-ONNX> —— ViT 系年龄/性别预测，ONNX 格式，检索片段显示可用 `transformers.js` 直接加载，输出年龄（0–100 截断）与性别。**许可未核实。**
  - **MobileAgeNet**：*"MobileAgeNet: Lightweight Facial Age Estimation for Mobile Deployment"*，PDF 见 <https://arxiv.org/pdf/2604.17007> —— 检索片段称**有界回归（bounded regression）+ 分阶段微调**做连续年龄预测。**完全未核实，仅作检索线索。**
  - **GitHub `smahesh29/Gender-and-Age-Detection`**：用 OpenCV Caffe 模型，档位同 §6.3 的 8 档。**不可达。**

### 6.7 【不确定】DEX

- 我只确认了它**作为 SSR-Net 的灵感来源被引用**（SSR-Net 论文逐字：*"Inspired by DEX, we address age estimation by performing multi-class classification and then turning classification results into regression by calculating the expected values."*；参考文献给出 Rothe et al. 2015 / 2016）。
- **DEX 的原文、MORPH-II MAE、模型体积、许可、权重可下载性：全部未核实。**
- **待办**：检索 Rothe R, Timofte R, Van Gool L. *DEX: Deep EXpectation of apparent age from a single image*, **ICCV Workshops 2015**，并打开 ChaLearn LAP 2015 挑战赛页面。

### 6.8 ★ 给团队的模型选型结论（**这是本节的实际产出**）

| 优先级 | 方案 | 建议动作 | 依据 |
|---|---|---|---|
| **① 首选验证** | **SSR-Net** | 先解决 **权重获取 + 许可** 两个未知，再在 PC 侧跑通，最后评估 A9 上的耗时 | **0.32 MB 是唯一有一手论文数字支撑的嵌入式可行体积**（§6.4） |
| **② 备选/对照** | **Levi & Hassner Caffe（OpenCV age_net）** | **立即可用**（作者页 Google Drive 直链 + OpenCV DNN 无需额外依赖）；输出 8 档，档位正好对齐年龄分层需求 | 官方权重可下载、架构极简、OpenCV 原生支持（§6.2/§6.3） |
| **③ 仅作 PC 侧参考** | **InsightFace genderage** | 可作离线标注/对照，但**许可为非商业研究**，**不可进产品** | ▼ 许可（§6.6） |
| **④ 仅作 PC 侧参考** | **DeepFace** | 同上；且 VGG-Face ~134 M 参数，PS 侧不可行 | 算力（§6.6） |
| **⑤ 场景不匹配** | **MiVOLO** | **不建议** —— 精度依赖人脸+人体双输入，VigiLens 只有脸 | 产品形态（§6.5） |
| **⑥ 待调研** | **DEX / MobileAgeNet / HF ONNX 模型** | 需要时再打开一手来源 | 未核实（§6.7、§6.6） |

> **⚠️ 一条工程纪律（建议直接写进 `docs/`）**：
> **报告任何年龄推断精度时，必须同时给出"基准数据集 + 输入分辨率 + MAE/准确率定义"三要素。**
> 反例（**禁止写法**）："DeepFace 年龄误差 ±4.65 岁"（没有基准）。
> 正例（**允许写法**）："DeepFace 0.0.95 在 IMDB-Clean 的 1000 张图、224×224 输入下 MAE = 10.83 岁（来源：arXiv:2511.14689）。"

### 6.9 本领域的检索式（可复现）

```
OpenCV age_net.caffemodel deploy_age.prototxt Levi Hassner 2015 age gender classification CNN 8 age bands Adience accuracy
DeepFace age model MAE InsightFace genderage.onnx buffalo_sc model size non-commercial license
SSR-Net compact soft stagewise regression network age estimation model size 0.32 MB MORPH-II MAE
MiVOLO age estimation github MORPH-II MAE license dual input
OpenCV age detection caffemodel 8 age groups 0-2 4-6 8-12 15-20 25-32 38-43 48-53 60-100 model file size MB
lightweight age estimation TFLite NCNN ONNX mobile embedded model age regression pretrained
```

---

## 7. 可借鉴的开源疲劳 / 困倦项目（**本节基本未能核实 —— 诚实声明**）

### 7.1 为什么本节几乎为空

**GitHub 全域在本机不可达**（DNS 解析到非公网 IP），而**开源困倦检测项目几乎全部托管在 GitHub**。
调研进行到本节时**检索配额也已耗尽**（HTTP 429）。因此：

> **本节列出的所有项目，我都没有打开过任何页面。全部标【不确定】。**
> **任何一条都不能作为引用来源写进团队文档。** 列出它们**仅为减少团队后续检索的起步成本**。

### 7.2 【不确定】仅作为"检索起点"的线索

我**唯一看到**的信息来自检索结果中对 GitHub Topics 页面的**摘要文字**（非页面本身）：

| 线索 | 检索摘要中的描述 | 置信度 |
|---|---|---|
| GitHub Topics: `perclos` <https://github.com/topics/perclos> | 出现"Real-time driver fatigue detection system built with Python, OpenCV and MediaPipe. Detects drowsiness using **EAR, MAR, PERCLOS, head pose estimation**..."这类描述 | **【不确定】** |
| GitHub Topics: `drowsiness-detection` <https://github.com/topics/drowsiness-detection> | "A real-time drowsiness detection system for drivers, which alerts the driver if they fall asleep due to fatigue while still driving." | **【不确定】** |
| GitHub Topics: `eye-aspect-ratio` <https://github.com/topics/eye-aspect-ratio> | 出现"Real-time driver drowsiness detection using **MediaPipe FaceMesh + CNN/BiLSTM hybrid**. Detects eye closure, yawning and head pose via facial..." | **【不确定】** |
| GitHub Topics: `driver-fatigue` <https://github.com/topics/driver-fatigue> | 描述与 `perclos` topic 高度重合 | **【不确定】** |
| `github.com/smahesh29/Gender-and-Age-Detection` | OpenCV 年龄/性别检测示例（见 §6.3） | **【不确定】** |

### 7.3 ★ **最重要的"未找到"结论（必须如实上报）**

> **我没有找到任何"已经做了年龄感知（age-aware）或个性化基线（personalised baselining）"的开源疲劳检测项目。**
>
> 所有可见描述都是**固定阈值 + EAR/MAR/PERCLOS + MediaPipe** 的常规组合，**没有**任何项目提到：
> - 按年龄分组校准阈值
> - 个体的个性化基线段（personal baseline / 自我对照）
> - 老化偏置（age bias）的处理
>
> **这对项目是"坏消息也是好消息"**：
> - **坏消息**：没有可直接抄的现成方案，年龄分层基线得自己做。
> - **好消息**：**"年龄感知 + 个性化基线 + 质量门控"这个组合，看起来是一个真实的差异化点**。而且它现在有文献依据（§3.2 的 Cai 2021 明确证明年龄改变了眼动指标与困倦的耦合关系）。

### 7.4 建议团队补充的检索式（本次因配额耗尽未执行）

```
# 在能访问 GitHub 的环境里执行：
github topic: perclos / drowsiness-detection / driver-fatigue / eye-aspect-ratio
# 关键词组合：
"personalized baseline" drowsiness detection
"personal calibration" PERCLOS driver
"age-aware" fatigue detection
"individual threshold" driver drowsiness eye
site:github.com drowsiness detection opencv age
# 非 GitHub 的替代检索：
"driver monitoring" open source library license commercial
openpilot driver monitoring attention           # 项目：comma.ai openpilot
seeingmachines driver state sensor
```

---

## 8. 【不确定】/ 未能核实 汇总（**不要拿去用**）

> 下面每一条都是**我尝试过但没成功**，或**存在矛盾说法**的项。团队要用，必须自己再核实一次。

### 8.1 数字 / 阈值类

| # | 未能核实的项 | 我做了什么 | 需要什么才能核实 |
|---|---|---|---|
| 1 | **PERCLOS 的 0.15 / 0.25 / 0.40 三级阈值的一手出处** | 多组检索式；只找到二手转述（Trejo et al. 2007 转述；Nature/PMC/ResearchGate 的表） | 打开 **Trejo et al. 2007** 或 **Grace et al. 1998 (DASC)** 原文；或 NHTSA/ FHWA 的相关报告全文 |
| 2 | **Levi & Hassner 模型在 Adience 上的准确率** | 打开了官方 PDF，但**正文在 "Table 1 lists the breakdown…" 处被截断**，结果表未读到 | 重新抓取该 PDF 的 4.1 节及后续结果表（或 CVF Open Access 的 HTML 版） |
| 3 | **`age_net.caffemodel` 的磁盘体积** | 检索；未找到一手说明 | 在能联网的机器上 `ls -l` 下到的文件 |
| 4 | **SSR-Net 在 MORPH-II 上的 MAE** | 打开 PDF 但被截断在架构小节 | 抓取 SSR-Net 论文的实验结果表 |
| 5 | **MiVOLO 的 MORPH-II MAE / 模型体积** | 只打开了 arXiv 摘要页 | 打开 arXiv HTML/PDF 全文（`arxiv.org/html/2307.04616v2`） |
| 6 | **DEX 的全部数字（MAE、体积、许可）** | 未检索 | 检索 Rothe et al., ICCVW 2015 + ChaLearn LAP 2015 |
| 7 | **Wilkinson 2013 中 `%LC` 的"眼睛全闭 > 10 ms"定义** | 读到该句，但 10 ms 在生理上疑似应为 1000 ms | 打开 JCSM 原文（DOI 10.5664/jcsm.3278）核对 PDF 原页 |
| 8 | **MRL Eye Dataset 的 licence** | 打开了官方页面；**页面未声明 licence** | 联系 Radovan Fusek，或查看数据包内是否附许可证文件 |

### 8.2 数据集"是否有年龄标注"类（**对本项目最关键**）

| # | 数据集 | 当前判定 | 需要什么才能定论 |
|---|---|---|---|
| 9 | **DMD** | 【不确定】 | 打开 GitHub README 与 arXiv 2008.12085，确认是否有 subject 级 age 字段 |
| 10 | **UTA-RLDD** | 【推测：无】 | 打开官方 Google Sites 页面（本次 429 / fetch fail）与 arXiv 1904.07312 全文 |
| 11 | **NTHU-DDD** | 【推测：无】 | **先确认官方页面 URL**（本次未能定位），再查其文档 |
| 12 | **YawDD** | 【不确定】 | 打开 IEEE DataPort 页面与 ACM 论文，查是否发布年龄字段 |
| 13 | **DROZY** | 【不确定】 | 打开 WACV 2016 论文全文（orbi.uliege.be 的 PDF） |
| 14 | **ZJU eyeblink** | 【推测：无】 | 打开 <http://www.cs.zju.edu.cn/~gpan/database/db_blink.html> |
| 15 | **是否有任何"既带困倦标注又带受试者年龄"的数据集** | **未找到** | 重点排查 **Drive&Act**（<https://driveandact.com/>），以及 **UL-DD**（arXiv 2507.13403，检索中出现的较新多模态困倦数据集） |
| 16 | **MegaAge-Asian / AFAD（亚洲人脸年龄数据集）** | 未核实 | 这两个对**中文用户群**最有价值（避免用 IMDB-WIKI 这种西方人脸为主的集），**建议列为高优先待办** |
| 17 | **MORPH-II / FG-NET / AgeDB 的内容、规模、许可、获取方式** | 未核实 | 各自官方页面（MORPH-II 需申请、可能收费） |

### 8.3 模型 / 权重类

| # | 未能核实的项 | 需要什么才能核实 |
|---|---|---|
| 18 | **SSR-Net 权重是否可下载、许可为何** | 访问 `github.com/shamangary/SSR-Net` 的 README 与 LICENSE |
| 19 | **MiVOLO 权重是否可下载、许可为何、体积** | 访问 `github.com/WildChlamydia/MiVOLO` |
| 20 | **Levi & Hassner Caffe 模型/权重的 licence** | 作者页只给 "Copyright 2015"；需查 repo `GilLevi/AgeGenderDeepLearning` 的 LICENSE，或直接邮件问作者 |
| 21 | **InsightFace genderage 权重的准确许可文本** | 打开 `github.com/deepinsight/insightface` 的 model zoo 说明页（非商业限制的确切措辞） |
| 22 | **HuggingFace `onnx-community/age-gender-prediction-ONNX` 的 licence 与体积** | 打开该 HF 模型卡 |
| 23 | **是否有 TFLite / NCNN 格式的年龄模型（含体积与许可）** | 检索 NCNN model zoo（`github.com/nihui/ncnn-android-...`；`Tencent/ncnn`）、TensorFlow Hub、以及 `onnx/models` 中的 age 模型 |

### 8.4 文献 / 量表类

| # | 未能核实的项 | 需要什么才能核实 |
|---|---|---|
| 24 | **SSS 的 7 个锚点措辞与量程** | 打开 Hoddes 1973 原文，或同一本 *STOP, THAT…* 的 SSS 章节 |
| 25 | **CFS 的条目数（14 vs 11）、选项与计分（0–3 还是 1–4）** | 打开 Chalder 1993 原文 + 修订版论文（如 PMC4529874） |
| 26 | **FSS 的 1–7 分锚点措辞** | 打开宾大 CBTF 的 FSS PDF（本次只读到检索片段） |
| 27 | **Trejo et al. 2007 的准确题录** | 检索并确认（可能是会议论文，非期刊） |
| 28 | **NHTSA Final Report DOT HS 808 640**（DOT-HS-808-762 摘要中提到） | 在 ROSA P 上检索该编号，它可能是 PERCLOS 早期分级阈值的真正出处 |
| 29 | **Duffy & Dijk / Dijk, Duffy & Czeisler 关于年龄与睡眠iness 的经典文献** | 本次未检索（配额耗尽）。**这是 §3 最明显的文献缺口** |
| 30 | **Philip / Sagaspe / Taillard 关于年轻 vs 老年驾驶员睡眠限制的研究** | 同上 |

---

## 9. 诚实边界

### 9.1 我实际完成了什么

- **实际打开并阅读的来源约 25 个**（Europe PMC API 题录/摘要、PMC 全文、ROSA P 官方落地页、arXiv 摘要与 HTML、官方数据集页面、作者官方项目页、期刊 PDF 等）。
- **任务要求"必须有一手来源"的 5 个领域（1、2、4、5、6）全部达标**：
  - **领域 1**：4 篇一手文献（Bentivoglio 1997 / Doughty 2001 / Sun 1997 / Sforza 2008）**【已验证】** + 1 篇（Wilkinson 2013）**【已验证】**
  - **领域 2**：Wierwille 1994 题录 + **Dinges & Grace 1998 官方落地页** + **DOT-HS-808-762 官方落地页** + Abe 2023 综述全文 **【已验证】**
  - **领域 4**：MRL Eye、CEW、DMD、Adience 官方页面 **【已验证】**；年龄标注字段判定给**明确否定结论**（MRL / CEW）
  - **领域 5**：KSS 锚点全文 + SSS/CFS/FSS 的 Europe PMC 权威题录 **【已验证】**
  - **领域 6**：Levi & Hassner 官方页与论文 PDF、Adience 官方页、SSR-Net 论文 PDF、MiVOLO arXiv 摘要、arXiv 2511.14689 全文 **【已验证】**
- **领域 3（年龄与困倦易感性）意外地成为最有价值的一节**：找到了 **Cai 2021** 这篇直接证据（实车 + 睡眠剥夺 + 双年龄组），其结论**恰好是"只有年轻组的 PERCLOS/眨眼时长/长闭眼随睡眠剥夺升高"**。

### 9.2 我**没有**完成的（以及为什么）

| 缺口 | 原因 | 影响 |
|---|---|---|
| **§7 开源项目：一个都没核实** | GitHub 全域 DNS 不可达 + 检索配额耗尽 | **无法提供任何可引用的开源项目**；"是否有年龄感知项目"的否定结论**只能算初步观察，不算核实** |
| **4 个疲劳数据集的官方页面未打开**（NTHU-DDD / UTA-RLDD / DROZY 正文 / ZJU） | 部分站点被拦截、部分检索配额耗尽 | §4 中它们均为【推测】 |
| **多个数据集的"是否有年龄标注"无法定论**（DMD/YawDD/DROZY/NTHU-DDD/UTA-RLDD） | 需要打开 GitHub README 或 PDF 全文 | **团队不能指望现成疲劳数据集提供年龄分层基线** |
| **全部"模型权重能否下载 / 许可为何"的问题** | GitHub 不可达 | §6 的体积与许可靠"未核实"，**选型只能先按"体积 + 输出格式"筛，许可必须另行确认** |
| **PERCLOS 0.15/0.25/0.40 的一手出处** | 检索到的全是二手转述 | **团队文档里暂时不要写这组阈值**，或写"待核实" |
| **Duffy/Dijk 等年龄-睡眠iness 经典文献** | 配额耗尽 | §3 的理论基础（为什么老年人更耐受）**缺少一手支撑** |
| **亚洲人脸年龄数据集（MegaAge-Asian / AFAD）** | 未检索 | **对中文用户群的年龄推断偏差风险完全未被评估** —— 这是 §6 最大的方法论缺口 |

### 9.3 达到"可确证"需要什么

1. **一个能访问 GitHub 的网络环境**（或至少能读 README/LICENSE 原文）—— 可一次性解决 §8.3 的全部 6 项 + §8.2 的 #9。
2. **能抓 PDF 正文的工具**（或人工下载阅读）—— 可解决 §8.1 的 #2/#4/#7、§8.2 的 #12/#13、§8.4 的 #24/#25/#26。
3. **能访问 PubMed / 出版社 DOI 页面** —— 可补齐 §8.4 的 #29/#30（Duffy/Dijk/Philip 等年龄-睡眠经典）。
4. **对 PERCLOS 阈值做一次专门的溯源检索**（Trejo 2007 → Grace 1998 → NHTSA DOT HS 808 640 这条链）。
5. **一次针对"年龄标注"的专项数据集检索**：`Drive&Act`、`UL-DD`（arXiv 2507.13403）、`MegaAge-Asian`、`AFAD`。
6. **自采数据**（最终无法回避）：任何公开数据集都替代不了"本项目的目标用户群、本项目的相机、本项目的场景"下的基线。**"年龄分层参考数据库"这个名字本身就暗示了它的最终形态是自建库，而不是引用一个现成的库。**

### 9.4 三条我认为团队最应该先做的事（按性价比排序）

1. **立刻把"Cai 2021 的年龄效应"写进设计文档。** 它是本次调研中**唯一一条会直接改变算法架构的发现**：不能对所有年龄用同一套 PERCLOS 阈值，且老年组需要"非 PERCLOS"的补充判据（head pose / inattention）。此事**零成本、零风险、有价值**。
2. **核对 MRL Eye 与 CEW 的许可，并把它们定位为"质量门控 + 眨眼检测"的数据来源，而不是年龄来源。** 两者都**没有年龄标签**（已确证），但 MRL 的 84,898 张真实驾驶红外眼图**对 VigiLens 的 `light_score` / 开闭眼判定极其对口**。
3. **在选定年龄模型之前，先解决许可。** 现在看起来最"顺手"的 InsightFace genderage 权重是**非商业研究用途**；而体积最合适的 SSR-Net 的**权重可获取性与许可都还是未知数**。**在许可没确认之前不要把它写进方案。**

---

## 10. 检索式总表（便于团队复现）

> 全部检索在 2026 年某日（本次会话）执行。工具：网页检索 + 页面抓取。
> **可复现的机器可读入口**：Europe PMC REST API
> `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=<QUERY>&resultType=core|lite&format=json`

| 领域 | 检索式 |
|---|---|
| **1 眨眼/年龄** | `Doughty 2001 blink rate survey normative consideration`；`Bentivoglio 1997 spontaneous blink rate physiology normal values`；`Sun 1997 age related blink rate study`；Europe PMC: `"spontaneous blinking" AND "optoelectronic"`；`TITLE:"blink rate" AND TITLE:"age"`；`TITLE:"blink rate" AND (children OR child)` |
| **2 PERCLOS** | `Dinges Grace 1998 PERCLOS valid psychophysiological measure of alertness psychomotor vigilance FHWA-MCRT-98-006`；`PERCLOS 0.15 0.25 0.40 threshold FHWA drowsiness alertness classification`；`Dinges Mallis Maislin Powell 1998 NHTSA DOT HS 808 762 ocular measurement index of fatigue alertness management`；`Wierwille Ellsworth 1994 PERCLOS P70 P80 EYEMEAS definition percentage of time eyelid closed 80 percent`；`"PERCLOS" threshold 0.15 origin drowsiness detection where does 0.15 come from`；`PERCLOS 0.4 alert 0.5 drowsy threshold heavy vehicle driver drowsiness detection system Grace 1998`；`Trejo 2007 PERCLOS drowsiness levels threshold 0.15 0.25 0.40 alert slightly drowsy` |
| **3 年龄×困倦** | `older drivers PERCLOS blink duration age differences drowsiness driving simulator study` |
| **4 数据集** | `NTHU-DDD driver drowsiness detection dataset subjects age annotation download licence`；`UTA-RLDD real-life drowsiness dataset 60 subjects age gender DROZY YawDD MRL eye dataset age labels`；`NTHU DDD drowsiness dataset download official 36 subjects`；`MRL eye dataset age gender glasses annotations filename`；`UTA Real-Life Drowsiness Dataset UTA-RLDD 60 subjects official download`；`DROZY database University Liege drowsiness 14 subjects age gender PSG KSS licence`；`YawDD yawning detection dataset NRC Canada 107 videos subjects age`；`CEW closed eyes in the wild dataset 2423 subjects official page`；`ZJU eyeblink dataset 80 subjects official download`；`DMD Driver Monitoring Dataset age gender subject metadata licence Politecnico Torino` |
| **5 量表** | `Åkerstedt Gillberg 1990 Karolinska Sleepiness Scale 9 point anchors extremely alert very sleepy fighting sleep`；`Chalder 1993 development of a fatigue scale 14 items Krupp 1989 fatigue severity scale 9 items anchors`；Europe PMC: `TITLE:"Quantification of sleepiness"`；`AUTH:"Chalder T" AND TITLE:"Development of a fatigue scale"`；`AUTH:"Krupp LB" AND TITLE:"fatigue severity scale"` |
| **6 年龄模型** | `OpenCV age_net.caffemodel deploy_age.prototxt Levi Hassner 2015 age gender classification CNN 8 age bands Adience accuracy`；`DeepFace age model MAE InsightFace genderage.onnx buffalo_sc model size non-commercial license`；`SSR-Net compact soft stagewise regression network age estimation model size 0.32 MB MORPH-II MAE`；`MiVOLO age estimation github MORPH-II MAE license dual input`；`OpenCV age detection caffemodel 8 age groups 0-2 4-6 8-12 15-20 25-32 38-43 48-53 60-100 model file size MB`；`lightweight age estimation TFLite NCNN ONNX mobile embedded model age regression pretrained` |
| **7 开源项目** | `open source driver drowsiness detection github PERCLOS personal baseline calibration mediapipe`；`NTHU driver drowsiness detection dataset official page download NTHU-DDD CV laboratory`；`open source drowsiness detection project github age-aware personalized threshold`；`openpilot driver monitoring camera attention open source MIT`；`opencv drowsiness detection github EAR PERCLOS age personalized baseline project` |

### 10.1 本笔记实际打开（获取内容）的来源清单

| # | 来源 | 类型 | 置信度贡献 |
|---|---|---|---|
| 1 | Europe PMC 记录：Bentivoglio 1997 | API 题录+摘要 | 【已验证】 |
| 2 | Europe PMC 记录：Doughty 2001 | API 题录+摘要 | 【已验证】 |
| 3 | Europe PMC 记录：Sun 1997 | API 题录+摘要 | 【已验证】 |
| 4 | Europe PMC 检索结果：Sforza 2008 | API 题录+摘要 | 【已验证】 |
| 5 | Europe PMC 记录：Wierwille & Ellsworth 1994 | API 题录 | 【已验证】 |
| 6 | ROSA P `dot/113`（Dinges & Grace 1998, FHWA-MCRT-98-006） | 官方落地页 | 【已验证】 |
| 7 | ROSA P `dot/2518`（DOT-HS-808-762） | 官方落地页 | 【已验证】 |
| 8 | PMC10108649（Abe 2023 综述，CC BY） | 期刊全文 | 【已验证】 |
| 9 | PMC3836343（Wilkinson 2013, JCSM） | 期刊全文 | 【已验证】 |
| 10 | PMC8566466（Cai 2021, Sci Rep, CC BY） | 期刊全文 | 【已验证】 |
| 11 | MRL Eye Dataset 官方页 | 数据集官方页 | 【已验证】 |
| 12 | CEW 官方页（NUAA） | 数据集官方页 | 【已验证】 |
| 13 | DMD 官方页（Vicomtech） | 数据集官方页 | 【已验证】 |
| 14 | Levi & Hassner 2015 官方项目页 | 作者官方页 | 【已验证】 |
| 15 | Levi & Hassner CVPR2015 论文 PDF | 一手论文 | 【已验证】（部分截断） |
| 16 | Adience 官方数据页 | 数据集官方页 | 【已验证】 |
| 17 | 宾大 CBTI：KSS 章节 PDF | 量表权威汇编 | 【已验证】 |
| 18 | Europe PMC 记录：Krupp 1989（FSS） | API 题录 | 【已验证】 |
| 19 | Europe PMC 记录：Hoddes 1973（SSS） | API 题录 | 【已验证】 |
| 20 | Europe PMC 记录：Chalder 1993（CFS） | API 题录 | 【已验证】 |
| 21 | SSR-Net IJCAI 2018 论文 PDF（NTU 站点） | 一手论文 | 【已验证】（部分截断） |
| 22 | arXiv 2307.04616 MiVOLO 摘要页 | 一手论文摘要 | 【已验证】 |
| 23 | arXiv 2511.14689（Jamo 2025, CC BY） | 一手论文全文 | 【已验证】 |
| 24 | arXiv 2408.12990（困倦检测综述） | 综述（部分截断） | 【推测】 |
| 25 | PyImageSearch：OpenCV 年龄检测教程 | 教程博客（二手） | 【推测】 |

---

*调研完成时间：本次会话。所有 URL 均为调研当时实际访问过的地址；若后续链接失效，请以标题 + DOI/报告编号为准重新检索。*
*本文件不改变项目任何阈值定义；`config.yaml` 仍是唯一阈值来源（`AGENTS.md` §3）。*
