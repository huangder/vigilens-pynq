#!/usr/bin/env bash
set -eu
W=${VG_DEPS:-/root/vigilens/autonomy-20261030}
OUT=.tmp/autonomy/deploy/test_face_detector_bounds
mkdir -p .tmp/autonomy/deploy
g++ -O1 -g -std=c++17 -fsanitize=undefined,float-cast-overflow -fno-sanitize-recover=all \
  -I "$W/ncnn-20240410/src" -I "$W/ncnn-host/src" \
  board/autonomy/tests/test_face_detector_bounds.cpp "$W/ncnn-host/src/libncnn.a" \
  -fopenmp -lpthread -lm -o "$OUT"
"$OUT"
