# VigiLens / 知倦：SD 冷启动拷贝包

本包把当前板端自治链路接入上电启动：BootROM → 指定PS初始化的U-Boot SPL → U-Boot → PL → Linux → USB相机 → 板端指标/心率估计 → HTTP页面。
无需PC生产测量数据。PC只需用浏览器访问板端页面。年龄旁路、PC录像等工具不属于原生板端功能；RR仍未实现。

**软件构建/核验与物理断电冷启动分开记录。交付清单中的`hardware_sd_boot_verified=false`表示实卡上电仍待你拷贝后验收。**
心率保留5秒有效积样与渐进滤波；缺测时页面标明历史值，首次测量显示“正在测量”，不保证5秒实际出数或准确性。

## 你最后只需拷贝的内容

1. 准备已有分区的SD卡：**第一主分区为FAT32**，留出至少256MiB空闲；当前脚本固定读取`mmc 0:1`，Linux挂载`/dev/mmcblk0p1`。
   本包不是整盘镜像，不会替你分区或格式化。先保留卡中旧启动文件的备份，避免混用旧`BOOT.BIN`、内核或模型。
2. 将**`copy_to_sd`目录里面的全部内容**拷到该分区根目录，不要把`copy_to_sd`作为外层目录一起拷入。
   根目录应直接出现：

   ```text
   BOOT.BIN
   u-boot.img
   boot.scr
   boot.cmd
   system.bit
   system.dtb
   zImage
   uInitrd
   vigilens/
     READY
     config.yaml
     boot.env
     app/                 程序、模型、库、网页、清单
   ```

3. 在Windows用本交付包外层的脚本核验。假设SD卡实际盘符为`E:`：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\Verify-Copy.ps1 -Root E:\
   ```

   请把`E:`替换为实际盘符。成功输出`COPY_CHECK_PASS`。脚本只读文件，不写卡；不传`-Root`则核验本机`copy_to_sd`目录。
4. 安全弹出卡。开发板断电，按Mizar-Z7板卡手册把启动模式设为**SD**，插卡并接上USB摄像头和网线，再接通电源。
   验收本次启动时关闭JTAG下载程序，不通过xsdb补发初始化、bitstream或内核。不要在未知拨码编号上猜位置。
5. 串口使用**115200，8N1**。预期启动标记依次包括`U-Boot SPL`、`U-Boot`、`VIGILENS_SD_BOOT`、内核、`VIGILENS_AUTOSTART_SCHEDULED`。
   标记是检查目标，不是本包已完成物理上电的证明。
6. PC与板连接到同一有线网段。默认板IP是**169.254.73.29/16**，当前项目PC使用169.254.73.28/16。
   浏览器打开 **http://169.254.73.29:8080/**；指标原文为`/api/latest`。无需启动PC算法或Python后端。

   页面复用`frontend/`当前仪表盘，板端打包时加入HTTP数据适配，自动连接真实指标并显示各项读数、趋势及拒报原因；不自动运行Mock，不需要PC后端。`/api/frame.jpg`仅提供RAM中的最新JPEG，质量取自`config.yaml:video_jpeg_quality`；
   RGB来自同一采集/分析帧，JPEG仅用于显示，不反馈给测量。帧缺失或过期返回503，页面隐藏旧画面并等待恢复。
   不在SD卡录制视频；预览刷新不等同于已验证的采集帧率或测量准确性。
   板端页面沿用拍摄页的紧凑布局：相机保持原比例，桌面显示13张指标卡，隐藏当前判断、底部眨眼状态条和未实现的呼吸率卡；页面保持真实HTTP数据源，不播放三组预设。原`frontend/`文件不改动。

## 上电后检查

登录串口后执行：

```sh
cat /proc/cmdline
cat /proc/mounts | grep /mnt/vigilens-boot
/usr/sbin/vigilens-service status
cat /run/vigilens/service.status
tail -n 20 /run/vigilens/supervisor.log
tail -n 20 /run/vigilens/camera.log
```

命令行应有`vigilens.autostart=1`，SD应用分区应是`ro`，服务应为`running`或明确的等待/错误原因。
没有相机时网页可显示等待连接，插入相机后自动尝试采集；`/api/latest`在无最新测量时返回拒绝/不可用状态是正常行为。
不要把历史数字、无脸运行或网页可打开当作当前有效心率或准确性验收。

操作服务：

```sh
/usr/sbin/vigilens-service stop
/usr/sbin/vigilens-service start
/usr/sbin/vigilens-service restart
/usr/sbin/vigilens-service check
```

相机退出、丢失或最新帧长时间不更新时，服务按配置重试；**质量拒报、HR为null不会触发重启**。
程序显式使用帧预算0持续运行，停止时发送信号释放相机/PL。服务只管理自己启动的进程，检测到其他相机占用时等待。
`/run/vigilens`中的日志滚动保留，重启即丢失；日常不把逐帧契约累积到SD或无界RAM文件，不保存原始人脸视频。
需要留证时由串口或HTTP主动导出标量日志。

## 配置与排错

参数源为项目根`config.yaml:cold_boot`，服务环境、图像尺寸、算法参数、包哈希均由构建导出。
板端使用编译时算法配置与内核内嵌的启动环境，**只改SD上的YAML或boot.env不会完整生效**；请回到项目重建整包再一起替换。
`boot.cmd`为可读源文件，实际由`boot.scr`执行；同样需重新生成，不能只改源文件。

- 串口完全无输出：先检查SD启动模式、第一FAT32分区、根目录`BOOT.BIN`及供电。保留真实串口输出，不先用JTAG掩盖缺口。
- U-Boot出现但内核未起：检查`boot.scr`、`u-boot.img`、`system.bit`、`zImage`、`uInitrd`和`system.dtb`是否全部同一包。
- Linux已起但未自启：查看`/run/vigilens-boot.log`，检查mmc枚举、只读挂载与READY标记。
- 网页打不开：检查网线、PC网段、`ifconfig eth0`、服务状态与`/run/vigilens/http.log`。
- 页面有数字：核对是否标了历史值；测量稳定性和准确性仍需要同步参考验证。

复现工具位于项目`board/cold_boot/`，使用本机已有WSL/Buildroot/模型缓存和AMD Bootgen，不需要外部下载，不自动格式化卡或写入SD/QSPI。
在仓库根执行`powershell -NoProfile -ExecutionPolicy Bypass -File board/cold_boot/Build-Delivery.ps1`可在新的时间戳目录重建。
`Generate-BootImage.ps1`用官方Bootgen把私有构建中的SPL打包，最终封包检查其载荷与原始SPL逐字节相同。
协作记录须由人类亲手补写。
