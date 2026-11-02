#!/bin/bash
# Native Linux queue regression; needs only gcc, pthreads and JPEG headers.
set -euo pipefail
repo=$(cd "$(dirname "$0")/../../.." && pwd)
br=${BUILDROOT_DIR:-/root/vigilens/buildroot}
out="$repo/.tmp/camera_pipeline_host"
mkdir -p "$out"
gcc -std=c11 -O2 -Wall -Wextra -Werror -fsanitize=address,undefined \
    "$repo/board/w7_buildroot/tests/dma_gray_test.c" -o "$out/test_dma_gray"
"$out/test_dma_gray"
# Dead-section removal excludes unused MMIO/JPEG code, so no ARM libraries
# or host libjpeg are linked. Worker/queue/finish functions are production code.
gcc -std=c11 -O1 -g -Wall -Wextra -Werror -pthread \
    -ffunction-sections -fdata-sections -fsanitize=address,undefined \
    -idirafter "$br/output/staging/usr/include" \
    "$repo/board/w7_buildroot/tests/test_camera_pipeline.c" \
    -Wl,--gc-sections -o "$out/test_camera_pipeline"
"$out/test_camera_pipeline"
