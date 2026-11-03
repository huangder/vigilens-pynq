#!/usr/bin/env bash
set -eu
# Cached dependencies only. No package/network operations. Invoke in repo root.
mode=${1:-host}
case "$mode" in host|arm) ;; *) echo "mode must be host or arm" >&2; exit 2;; esac
out=${2:-.tmp/autonomy/native_vision}
deps=${VG_DEPS:-/root/vigilens/autonomy-20261030}
config=${VG_CONFIG:-config.yaml}
runtime=${3:-native}
case "$runtime" in native) runtime_source=vision_runtime;; tracked|tracked_contours) runtime_source=tracked_runtime;; ert|ert_translation|ert_hybrid|ert_temporal) runtime_source=ert_runtime;; *) echo "runtime must be native, tracked, tracked_contours, ert, ert_translation, ert_hybrid or ert_temporal" >&2; exit 2;; esac
runtime_flags=""
vision_flags=""
if [ "$runtime" != native ]; then runtime_flags=-DVIGI_TRACKED_RUNTIME; fi
if [ "$runtime" = tracked_contours ]; then vision_flags=-DVIGI_CONTOUR_TRACKER; fi
if [ "$runtime" = ert_translation ]; then vision_flags=-DVIGI_ERT_TRANSLATION_ROI; fi
if [ "$runtime" = ert_hybrid ] || [ "$runtime" = ert_temporal ]; then vision_flags=-DVIGI_ERT_HYBRID; fi
if [ "$runtime" = ert_temporal ]; then vision_flags="$vision_flags -DVIGI_ERT_TEMPORAL"; fi
mkdir -p "$out/$mode"
python3 board/autonomy/export_config.py --config "$config" --output "$out/vg_config.h"
if [ -n "${VG_FIR_HEADER:-}" ];then
    python3 board/autonomy/export_vital_config.py --config "$config" --output "$out/vg_vital_config.h" --fir-header "$VG_FIR_HEADER" --board-profile
else
    python3 board/autonomy/export_vital_config.py --config "$config" --output "$out/vg_vital_config.h" --board-profile
fi
python3 board/autonomy/export_vision_config.py --config "$config" --output "$out/vg_vision_config.h"
if [ "$mode" = host ]; then
    cc=gcc; cxx=g++; linker=g++
    ncnn="$deps/ncnn-host/src"
    mnn="$deps/mnn/host/mnn-runtime"
    arch=""; extra="-fopenmp"; link_arch=""
else
    tool="$deps/gcc-arm-8.3-2019.03-x86_64-arm-linux-gnueabihf"
    cc="$tool/bin/arm-linux-gnueabihf-gcc"
    cxx="$tool/bin/arm-linux-gnueabihf-g++"; linker="$cxx"
    ncnn="$deps/ncnn-arm/ncnn-runtime/src"
    mnn="$deps/mnn/arm/mnn-runtime"
    arch="-mcpu=cortex-a9 -mfpu=neon -mfloat-abi=hard"
    link_arch="-no-pie"
    extra="-static-libstdc++ -static-libgcc -fopenmp"
fi
objects=""
for source in behavior quality decision measure vital pose_pnp contract_json autonomy_engine detect_policy; do
    "$cc" -O2 -std=c11 -fPIC -Wall -Wextra -Werror $arch -I board/autonomy -I "$out" \
        -c "board/autonomy/$source.c" -o "$out/$mode/$source.o"
    objects="$objects $out/$mode/$source.o"
done
sources="face_detector landmark_runner $runtime_source point_tracker vision_replay"
model_lib="$mnn/libMNN.a"
dlib_flags=""
if [ "$runtime_source" = ert_runtime ]; then
    dlib=${VG_DLIB:-.tmp/autonomy/ert_20261102/dlib-19.24.6}
    test -f "$dlib/dlib/image_processing/shape_predictor.h"
    sources="face_detector $runtime_source vision_replay"
    if [ "$runtime" = ert_hybrid ] || [ "$runtime" = ert_temporal ]; then sources="$sources point_tracker"; fi
    model_lib=""
    dlib_flags="-isystem $dlib -DDLIB_NO_GUI_SUPPORT -DNDEBUG"
    "$cxx" -O3 -std=c++17 -fPIC $arch $dlib_flags -c "$dlib/dlib/test_for_odr_violations.cpp" -o "$out/$mode/dlib_odr.o"
    objects="$objects $out/$mode/dlib_odr.o"
fi
for source in $sources; do
    optimization=-O2
    if [ "$source" = point_tracker ] || [ "$source" = ert_runtime ]; then optimization=-O3; fi
    "$cxx" "$optimization" -std=c++17 -fPIC -Wall -Wextra -Werror $arch $vision_flags $dlib_flags \
        -I board/autonomy -I "$out" -I "$deps/ncnn-20240410/src" -I "$ncnn" \
        -I "$deps/mnn/source/MNN-3.6.1/include" \
        -c "board/autonomy/$source.cpp" -o "$out/$mode/$source.o"
    if [ "$source" != vision_replay ]; then
        objects="$objects $out/$mode/$source.o"
    fi
done
"$linker" $link_arch -o "$out/vg_vision_replay_$mode" $objects "$out/$mode/vision_replay.o" \
    -Wl,--start-group "$ncnn/libncnn.a" $model_lib $extra -Wl,--end-group -lpthread -ldl -lm
echo "NATIVE_VISION_BUILD_PASS mode=$mode runtime=$runtime executable=$out/vg_vision_replay_$mode"
"$cc" -O2 -std=c11 -Wall -Wextra -Werror $arch -I "$out" \
    board/autonomy/metrics_http.c -lm -o "$out/vg_metrics_http_$mode"
if [ "$mode" = arm ]; then
    camera_objects=""
    for source in uvc_probe camera_pl; do
        "$cc" -O2 -std=gnu11 $arch $runtime_flags -DVIGI_CAMERA_PL -DVIGI_AUTONOMY -DVIGI_CONFIGURED_CAMERA \
            -I board/w7_buildroot -I board/autonomy -I "$out" \
            -idirafter /root/vigilens/buildroot/output/host/arm-buildroot-linux-gnueabihf/sysroot/usr/include \
            -c "board/w7_buildroot/$source.c" -o "$out/$mode/$source.o"
        camera_objects="$camera_objects $out/$mode/$source.o"
    done
    for source in camera_local autonomy_camera camera_preview; do
        "$cc" -O2 -std=c11 -Wall -Wextra -Werror $arch -DVIGI_CAMERA_PREVIEW \
            -I board/w7_buildroot -I board/autonomy -I "$out" \
            -idirafter /root/vigilens/buildroot/output/host/arm-buildroot-linux-gnueabihf/sysroot/usr/include \
            -c "board/autonomy/$source.c" -o "$out/$mode/$source.o"
        camera_objects="$camera_objects $out/$mode/$source.o"
    done
    "$linker" $link_arch -o "$out/vigi_camera_autonomy" $objects $camera_objects \
        -Wl,--start-group "$ncnn/libncnn.a" $model_lib \
        /root/vigilens/buildroot/output/target/usr/lib/libjpeg.a $extra \
        -Wl,--end-group -lpthread -ldl -lm
    echo "NATIVE_CAMERA_BUILD_PASS executable=$out/vigi_camera_autonomy"
    # Same pixel path/compiler/JPEG library, no analysis. Benchmark baseline;
    # this target is never substituted for the production measurement program.
    for source in uvc_probe camera_pl; do
        "$cc" -O2 -std=gnu11 $arch -DVIGI_CAMERA_PL -DVIGI_CONFIGURED_CAMERA \
            -I board/w7_buildroot -I board/autonomy -I "$out" \
            -idirafter /root/vigilens/buildroot/output/host/arm-buildroot-linux-gnueabihf/sysroot/usr/include \
            -c "board/w7_buildroot/$source.c" -o "$out/$mode/${source}_plonly.o"
    done
    "$cc" $link_arch -o "$out/vigi_camera_pl_only" "$out/$mode/uvc_probe_plonly.o" \
        "$out/$mode/camera_pl_plonly.o" "$out/$mode/camera_local.o" \
        /root/vigilens/buildroot/output/target/usr/lib/libjpeg.a -lpthread -lm
    echo "PL_ONLY_BUILD_PASS executable=$out/vigi_camera_pl_only"
fi
python3 - "$out" "$runtime" "$mode" <<'PY'
import hashlib,json,sys
from pathlib import Path
out=Path(sys.argv[1]);runtime,mode=sys.argv[2:]
record={'runtime':runtime,'mode':mode,'config_sha256':json.loads((out/'vg_vision_config.json').read_text())['config_sha256']}
if mode=='arm':record['camera_sha256']=hashlib.sha256((out/'vigi_camera_autonomy').read_bytes()).hexdigest()
(out/'runtime_build.json').write_text(json.dumps(record,indent=2)+'\n')
PY
