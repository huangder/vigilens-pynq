# 本地 MTCNN 检测器

`face_detector.h/.cpp` 为 ncnn 的 det1 → det2 → det3 级联提供 C ABI。
输入为 RGB888 原图，支持按字节指定行步长；输出为裁剪到图内的整数
`[x1,y1,x2,y2)` 半开区间和 ONet 分数，按分数降序排列。调用方按需要选最大脸。
`vg_fd` 实例须由一个线程独占；函数不保留历史框，返回 0 即本帧没有检测结果。
返回负数为参数或推理错误，调用方不可继续消费上一次输出数组。

所有运行阈值、金字塔参数和候选预算均由 `vg_fd_params` 显式传入；
产品调用方使用根 `config.yaml` 的 `board_vision` 和 `export_vision_config.py`。
模型固有输入尺寸为 12 / 24 / 48，归一化为 `(RGB-127.5)/128`。
阶段间采用边界零填充，避免先裁到图内再缩放导致边缘脸变形。
候选预算会截断各层 PNet NMS 和后续全局 NMS 的输出，影响拥挤场景召回；尚未标定。

模型为仓库已有 `.tmp/autonomy/mtcnn/det{1,2,3}.{param,bin}`，
来源为本机缓存 `PFLD-DEMO.zip`；已有来源审计见 `lightweight_candidates.md`
“原 PFLD 论文作者 0.25X 演示”段，链接到作者项目页。
本次没有下载模型、没有上传视频；本次没有进一步确认这组权重的再分发许可。
模型不自动下载，也不因加载失败回退到固定框。

【已验证，2026-11-01】以下 WSL 构建均退出 0（本地已缓存 ncnn 20240410 和 ARM GCC）：

```powershell
wsl -d Ubuntu -- bash board/autonomy/build_face_detector.sh host
wsl -d Ubuntu -- bash board/autonomy/build_face_detector.sh arm
```

输出 `.tmp/autonomy/deploy/libvgfd-host.so` / `libvgfd-arm.so` 和
`vg_pnet-host` / `vg_pnet-arm`。也可将 `face_detector.cpp` 直接加入组合二进制，
使用 ncnn 源码头文件、对应构建目录头文件和静态 `libncnn.a`，并链接 `-fopenmp -lpthread -lm`。
ARM 共享库实际依赖 `libpthread.so.0`、`libm.so.6`、`libgomp.so.1`、`libc.so.6`、
`ld-linux-armhf.so.3`；脚本静态链接 libstdc++ / libgcc。

可复现本地离线检查（PowerShell 准备帧，WSL 运行共享库）：

```powershell
. .\env.ps1
python board/autonomy/export_vision_config.py --output .tmp/autonomy/detector_test/vg_vision_config.h
python board/autonomy/tests/check_face_detector.py prepare --work .tmp/autonomy/detector_test --video data/raw/blink.mp4 --reference .tmp/autonomy/calibration/peppa/manifest.json
wsl -d Ubuntu -- python3 board/autonomy/tests/check_face_detector.py run --work .tmp/autonomy/detector_test --library .tmp/autonomy/deploy/libvgfd-host.so --models .tmp/autonomy/mtcnn --params .tmp/autonomy/detector_test/vg_vision_config.json
```

【已验证】默认选取真实 blink 视频帧 0、30、60、150、300、450、570，各返回一个框；
重复推理结果逐位一致，额外 13 字节行 padding 与 packed RGB 结果一致，
纯黑图返回 0，非法 `scale_factor=1` 返回 -1。
第一帧框为 `[226,112,418,362)`，同一帧历史 MediaPipe 开发对照框为
`xywh=[216,101,218,294]`。两者框口径不同，MediaPipe 不是人工真值。
原始结果与模型/库 SHA256 保存到 `.tmp/autonomy/detector_test/results.json`，
提取的原始帧仅留在忽略目录，不应提交。

【不确定 / 未验证】真人检测准确率、侧脸与多人场景、目标 ARM 上的耗时与内存峰值。
主机耗时不能推断 Cortex-A9 的实时能力。组合管线和板上运行由主代理另外验证。

旧 `pnet_probe.cpp` 继续仅作 PNet 诊断：修正了 x2/y2 缺失 12 像素感受野、
NMS 未按分数排序、RGB/BGR 文档矛盾与倒序金字塔问题；CLI 现在要求显式
`threshold nms min_face factor`。完整部署接口应使用三阶段检测器。

## 候选框在当前帧重新验证

`vg_fd_refine(ctx,rgb,w,h,stride,&params,&candidate,output,capacity)` 接受一个
图内半开候选，忽略其旧分数，先按最大边方形化，再执行当前帧的 RNet / ONet。
与全图检测共用相同回归、NMS 和输出裁剪代码；不运行 PNet，不增加扩框常数。
返回 0 / 1 或负数，输入候选与输出允许指向同一对象。
返回 0 时调用方应立即执行全图检测；候选本身绝不可作为“本帧已检测”的输出。
调用方还应按时间周期执行全图检测，以允许目标离开旧框范围后重新发现。
周期由产品配置决定，本接口不保存计时器或历史图像。

【已验证，2026-11-01】新增接口后，host / ARM 构建均退出 0。离线测试选取
33 帧：0～20、150～152、300～302、450～452、570～572。28 次相邻帧候选验证
中 27 次返回一个框；第 452 帧返回 0，同帧全图检测有框。这是对“失败立即全图”
路径的真实开发案例，不是检测准确率。黑图返回 0，非法候选返回 -1；
重复运行、带 padding 行步长与输入输出原址调用结果一致。
原始结果在 `.tmp/autonomy/detector_refine_test/results.json`，包含全图与 refine
框的位置开发对照、主机耗时和模型 / 库哈希。没有测量目标板的 refine 耗时。

新增测试可复现如下（先按上文导出配置并构建）：

```powershell
python board/autonomy/tests/check_face_detector.py prepare --work .tmp/autonomy/detector_refine_test --video data/raw/blink.mp4 --reference .tmp/autonomy/calibration/peppa/manifest.json --frames 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,150,151,152,300,301,302,450,451,452,570,571,572
wsl -d Ubuntu -- python3 board/autonomy/tests/check_face_detector.py run --work .tmp/autonomy/detector_refine_test --library .tmp/autonomy/deploy/libvgfd-host.so --models .tmp/autonomy/mtcnn --params .tmp/autonomy/detector_test/vg_vision_config.json --check-refine
```

## 畸形回归与参数边界

【已验证，2026-11-01】补齐 crop 的所有坐标双侧范围检查后，模型回归产生的
NaN / Inf / 极大有限值在转换为整数之前被拒绝；RNet / ONet 的非有限 offset
也会丢弃对应候选。最终框需具有有限且递增的坐标。资源保护拒绝
`max_candidates > 65536` 或需超过 256 级全图金字塔的参数，返回 -1。
这些是接口防失控上限，不是分类阈值或产品默认值。

```powershell
wsl -d Ubuntu -- bash board/autonomy/tests/check_face_detector_bounds.sh
```

实际输出 `FACE_DETECTOR_BOUNDS_PASS`，退出 0。该测试以
`-fsanitize=undefined,float-cast-overflow -fno-sanitize-recover=all` 编译本检测源码，
注入各坐标与回归 offset 的 NaN / Inf / FLT_MAX / 极大有限值，另检查
非法整数框、INT_MAX 候选预算和最接近 1 的较小 float 比例。测试不加载模型，
未重编或插桩 ncnn 静态库；其结论限于当前检测包装代码的已覆盖路径。

## 可选 packed 计算路径

`vg_fd_create_ex(dir,threads,packing)` 只接受 packing=0 或 1，其他值返回 NULL。
原 `vg_fd_create(dir,threads)` 继续等价于 packing=0；没有改变旧调用方行为。
此开关只设置三个 ncnn Net 的 `opt.use_packing_layout`，不改阈值、输入、
回归、NMS 或像素口径。默认 `extract(type=0)` 会解包输出，解码代码保持一致。

【已验证，2026-11-01】host / ARM 两档构建退出 0；同一主机、模型、参数与
上述 33 帧素材分别运行 packing=0/1，full 和 refine 的输出框数均无变化。
full 的 5 帧（16、18、19、451、572）出现 bbox 坐标变化，最大单坐标差 8 px，
最大 score 绝对差 0.001028299331665039；28 次链式 refine 最大单坐标差 26 px，
最大 score 绝对差 0.00535851716995239。链式结果包含上一次候选框差异的累积，
不能把全部差异当作单帧模型误差。452 帧两种模式均返回 refine=0。
两种模式分别通过黑图、重复性、行 padding、输入输出 alias 和非法 packing 检查；
NaN / Inf / 极大 bbox 的包装代码边界测试也对两种 packing 标志执行。

原始双模式结果为 `.tmp/autonomy/detector_refine_test/packing0.json` 与 `packing1.json`，
聚合差异为同目录 `packing_comparison.json`。这些是主机指令集上的开发对照，
不能证明 ARM 数值等价、检测准确率或板端加速。产品需保留开关并独立板测。

```powershell
wsl -d Ubuntu -- python3 board/autonomy/tests/check_face_detector.py run --work .tmp/autonomy/detector_refine_test --library .tmp/autonomy/deploy/libvgfd-host.so --models .tmp/autonomy/mtcnn --params .tmp/autonomy/detector_test/vg_vision_config.json --check-refine --packing 0 --result-name packing0.json
wsl -d Ubuntu -- python3 board/autonomy/tests/check_face_detector.py run --work .tmp/autonomy/detector_refine_test --library .tmp/autonomy/deploy/libvgfd-host.so --models .tmp/autonomy/mtcnn --params .tmp/autonomy/detector_test/vg_vision_config.json --check-refine --packing 1 --result-name packing1.json
wsl -d Ubuntu -- python3 board/autonomy/tests/compare_detector_packing.py .tmp/autonomy/detector_refine_test/packing0.json .tmp/autonomy/detector_refine_test/packing1.json --output .tmp/autonomy/detector_refine_test/packing_comparison.json
```
