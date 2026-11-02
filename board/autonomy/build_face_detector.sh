#!/usr/bin/env bash
set -eu
# Run inside WSL, repository root. Cached ncnn build; override paths if needed.
W=${VG_DEPS:-/root/vigilens/autonomy-20261030}
MODE=${1:-host}
OUT=${2:-.tmp/autonomy/deploy}
mkdir -p "$OUT"
if [ "$MODE" = host ]; then
  CXX=g++
  LIB="$W/ncnn-host/src"
  EXTRA="-fopenmp"
else
  CXX="$W/gcc-arm-8.3-2019.03-x86_64-arm-linux-gnueabihf/bin/arm-linux-gnueabihf-g++"
  LIB="$W/ncnn-arm/ncnn-runtime/src"
  EXTRA="-fopenmp -static-libstdc++ -static-libgcc"
fi
"$CXX" -O2 -std=c++17 -fPIC -shared -I "$W/ncnn-20240410/src" -I "$LIB" \
  board/autonomy/face_detector.cpp "$LIB/libncnn.a" $EXTRA -lpthread -lm \
  -Wl,--no-undefined -o "$OUT/libvgfd-$MODE.so"
"$CXX" -O2 -std=c++17 -I "$W/ncnn-20240410/src" -I "$LIB" \
  board/autonomy/pnet_probe.cpp "$LIB/libncnn.a" $EXTRA -lpthread -lm \
  -o "$OUT/vg_pnet-$MODE"
