#!/usr/bin/env bash
# Reproducible Cortex-A9 model-only probe. Source trees stay external/ignored.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
: "${VG_TENSORFLOW_SOURCE:?set the TensorFlow v2.16.1 source directory}"
: "${VG_MEDIAPIPE_SOURCE:?set the MediaPipe v0.10.21 source directory}"
: "${VG_ARM_TOOLCHAIN:?set the ARM C++ compiler bin directory}"
: "${VG_ARM_C_COMPILER:?set a C compiler supporting the XNNPACK ACLE SIMD32 builtins}"
: "${VG_ARM_SYSROOT:?set the sysroot matching the target board libc}"
out=${1:?usage: build_optimized_tflite.sh output-directory}
cache=${VG_DEP_CACHE:-$repo/.tmp/autonomy/deps}
cmake=${CMAKE:-cmake}
python=${PYTHON_HOST:-python3}
"$python" "$repo/board/autonomy/fetch_tflite_deps.py" \
  --tensorflow-source "$VG_TENSORFLOW_SOURCE" --cache "$cache"
"$cmake" -C "$cache/preload-wsl.cmake" -S "$repo/board/autonomy" -B "$out" \
  -DCMAKE_TOOLCHAIN_FILE="$repo/board/autonomy/armv7.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF \
  -DVG_TENSORFLOW_SOURCE="$VG_TENSORFLOW_SOURCE" \
  -DVG_MEDIAPIPE_SOURCE="$VG_MEDIAPIPE_SOURCE" \
  -DVG_ENABLE_XNNPACK=ON -DVG_ENABLE_RUY=ON \
  -DXNNPACK_ENABLE_ARM_FP16_SCALAR=OFF -DXNNPACK_ENABLE_ARM_FP16_VECTOR=OFF \
  -DXNNPACK_ENABLE_ARM_BF16=OFF -DXNNPACK_ENABLE_ARM_DOTPROD=OFF \
  -DXNNPACK_ENABLE_ARM_I8MM=OFF -DXNNPACK_ENABLE_JIT=OFF \
  -DXNNPACK_ENABLE_ASSEMBLY=ON
"$cmake" --build "$out" --target vg_model_probe -j "${VG_BUILD_JOBS:-4}"
