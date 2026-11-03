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
gcc -std=c11 -O2 -Wall -Wextra -Werror \
    "$repo/board/w7_buildroot/tests/capture_fps_test.c" -o "$out/test_capture_fps"
"$out/test_capture_fps"
# Dead-section removal excludes unused MMIO/JPEG code, so no ARM libraries
# or host libjpeg are linked. Worker/queue/finish functions are production code.
for buffers in 6 8; do
    gcc -std=c11 -O1 -g -Wall -Wextra -Werror -pthread \
        -ffunction-sections -fdata-sections -fsanitize=address,undefined \
        -DVG_RGB_POOL_BUFFERS="$buffers" \
        -idirafter "$br/output/staging/usr/include" \
        "$repo/board/w7_buildroot/tests/test_camera_pipeline.c" \
        -Wl,--gc-sections -o "$out/test_camera_pipeline_$buffers"
    "$out/test_camera_pipeline_$buffers"
done
