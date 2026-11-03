# Heart-rate source timing diagnosis and 45 Hz candidate

2026-11-04，Asia/Shanghai。实验室趋势监测原型；以下不作人体准确率或医疗结论。

本文保留45 Hz候选的诊断沿革。后续用户将原始采样下限设为30 Hz，并增加低值过滤及浏览器历史；当前状态见 [hr_signal_history.md](hr_signal_history.md)，不能把下面的45 Hz包视为最新运行程序。

## 现场原因

【已验证】旧板端 `ear300` 程序 SHA-256 为 `6dcaafd589dc4dd3e5a897ddfa44c0192eb7530d5c438705ec7ab4efb7915f5c`，配置的处理率为60 Hz。串口读取当前及轮转日志，覆盖时间戳12758.244083～12836.614415：4017个分析观测、3917个人脸可见观测，按首尾采集时间戳计算分析速率51.243881 Hz，最大历史550点。

该段拒报/输出日志计数：采样过慢2575、无主峰440、暖机上下文不可用362、未积满296、短窗低置信174、质量/失脸阻断100、倍频歧义10、有效原始HR 60。计数是分析帧记录，并非独立心率测量或准确率。

旧门限为 `60*(1-rppg_fs_tol)=54 Hz`。即使窗口已经积满，实际输入约51 Hz仍会在频谱计算前被拒绝。现有实现其实先按采集时间戳重采样、再在60 Hz网格滤波/计算频谱，原始输入率与处理网格需要分别校验。

## 修复候选

用户指定 `board_vision.vital_source_min_hz=45.0`。这是原始真实样本密度下限；相机目标、重采样网格和125 taps FIR继续60 Hz，75个真实有效样本的启动下限不变。插值点不计作真实采样，不把实际约51 Hz分析吞吐称为完整60 fps。

仅显式启用的时间戳模式使用该输入下限；PC/旧参考与未启用模式保留原标称容差。低于45 Hz、异常高输入率、非单调时间、长缺口、超限抖动及频谱不合格仍拒报。C/Python及ctypes同步，JSON/schema和正式契约版本不变，未代签。45 Hz为用户工程候选，尚未标定人体误差。

另将重采样的暖机上下文限制到FIR/POS实际需要的历史，避免把更久的旧前缀全部展开；保留必要的边界采样，C/Python对拍验证。

## 同类项目对照

| 项目 | 实际读到的实现 | 对本项目的意义 |
|---|---|---|
| [pyVHR](https://github.com/phuselab/pyVHR) | 示例采用6秒窗；[窗口工具](https://github.com/phuselab/pyVHR/blob/master/pyVHR/extraction/utils.py)支持按秒定义重叠窗口/步长；[Welch实现](https://github.com/phuselab/pyVHR/blob/master/pyVHR/BPM/utils.py)明确使用传入fps计算频率，支持多区域处理 | 重叠窗口方向一致；需要正确的时间基准。6秒是其示例参数，不能直接当作本项目最优值或迁移其性能 |
| [rPPG-Toolbox](https://github.com/ubicomplab/rPPG-Toolbox) | [UBFC配置](https://github.com/ubicomplab/rPPG-Toolbox/blob/main/configs/infer_configs/UBFC-rPPG_UNSUPERVISED.yaml)使用30 Hz；该配置关闭较小评估窗，因此不能把其中WINDOW_SIZE=10称作生效的10秒窗。[后处理](https://github.com/ubicomplab/rPPG-Toolbox/blob/main/evaluation/post_process.py)用传入fs计算频谱；[POS实现](https://github.com/ubicomplab/rPPG-Toolbox/blob/main/unsupervised_methods/methods/POS_WANG.py)有1.6秒局部投影窗 | 处理率须与数据相配；项目并不要求所有算法必须60 Hz。当前板端75/60=1.25秒启动小于POS局部窗，不能仅切换方法名就套用其效果 |

【推测】后续可在同一匿名RGB统计序列上比较Green/POS/CHROM及Welch，分离时基、ROI和谱质量影响；需要本地采集标量序列才能做算法实测对比。本轮只做源码/文档对照，未运行这些外部框架，也未上传人脸媒体。

## 验证边界

合成回归覆盖45.1、48、51.24和60 Hz输入的同一物理频率；44.9 Hz及更低密度仍拒报。另验证非整数FFT频点的分辨率、长缺口、抖动、白噪声、默认严格模式及30/60 Hz原生引擎。合成刺激不能作为人体测量成绩。

候选只部署到 `/tmp/vigilens-sampling45`，运行状态在 `/run/vigilens-sampling45`；保留旧60 Hz开发服务作回退，SD冷启动包未修改。程序/配置哈希、串口和HTTP实测见本轮证据。

【已验证：45 Hz现场复测】首次60秒采集300个唯一HTTP指标观测，其中212个识别人脸、2个HR非空；用户随后明确确认人脸识别稳定并正对相机，第二次60秒采集300个唯一指标观测，其中273个识别人脸、41个HR非空。两段均无请求错误、无契约错误，帧号和时间戳递增。HTTP观测不是独立HR估计，两次现场状态不同，不据此计算改善比例。

第二段对应的轮转日志在读取时只保留时间戳14211.779645～14233.530789，约21.75秒，不能将其当作完整60秒诊断：1077个分析帧、1076个人脸可见帧，分析速率49.468662 Hz；采样过慢17、无主峰647、短窗低置信109、倍频歧义22、未积满74、失脸/质量阻断1、有效输出199、输出不稳定8。原始HR有限207帧，经过输出门控后有效199帧。最大滚动历史525点。

【结论边界】配置修改及RAM部署已经实测，45 Hz下限生效；当前仍有合法的低密度拒报，且频谱主峰不足成为该保留片段的主要拒报原因。尚未实现连续有效HR；进一步改善需本地匿名RGB统计、ROI/光照/运动分析及同步参考，不能继续仅靠降低采样门槛解决。当前RAM开发包断电后不会保留为最新SD交付。
