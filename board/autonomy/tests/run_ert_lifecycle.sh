#!/usr/bin/env bash
# Host-only model/worker lifecycle regression. No network, camera or board I/O.
# VG_DLIB must name a local dlib v19.24.6 source tree; raw fixture stays local.
set -eu
model=${1:?usage: run_ert_lifecycle.sh model.dat supplied_roi.rgbstream [output]}
fixture=${2:?local supplied-ROI fixture required}
out=${3:-.tmp/autonomy/ert_lifecycle}
dlib=${VG_DLIB:?set VG_DLIB to a local dlib v19.24.6 source tree}
sanitize=${VIGI_TEST_SANITIZE:-0}
case "$sanitize" in 0) flags="";; 1) flags="-fsanitize=address,undefined -fno-omit-frame-pointer";; *) echo 'VIGI_TEST_SANITIZE must be 0 or 1' >&2; exit 2;; esac
variant=${VIGI_ERT_TEST_VARIANT:-ert}
case "$variant" in ert) variant_flags="";; ert_hybrid) variant_flags="-DVIGI_ERT_HYBRID";; ert_temporal) variant_flags="-DVIGI_ERT_HYBRID -DVIGI_ERT_TEMPORAL";; *) echo 'VIGI_ERT_TEST_VARIANT must be ert, ert_hybrid or ert_temporal' >&2; exit 2;; esac
test -f "$model"
test -f "$fixture"
test -f "$dlib/dlib/test_for_odr_violations.cpp"
mkdir -p "$out"
python3 board/autonomy/export_config.py --config config.yaml --output "$out/vg_config.h"
python3 board/autonomy/export_vital_config.py --config config.yaml --output "$out/vg_vital_config.h"
python3 board/autonomy/export_vision_config.py --config config.yaml --output "$out/vg_vision_config.h"
objects=""
for source in behavior quality decision measure vital pose_pnp contract_json autonomy_engine detect_policy; do
    gcc -O2 -std=c11 -Wall -Wextra -Werror $flags -I board/autonomy -I "$out" \
        -c "board/autonomy/$source.c" -o "$out/$source.o"
    objects="$objects $out/$source.o"
done
if [ "$variant" = ert_hybrid ] || [ "$variant" = ert_temporal ]; then
    g++ -O2 -std=c++17 -Wall -Wextra -Werror $flags -I board/autonomy -c board/autonomy/point_tracker.cpp -o "$out/point_tracker.o"
    objects="$objects $out/point_tracker.o"
fi
g++ -O2 -std=c++17 -DDLIB_NO_GUI_SUPPORT -Wall -Wextra -Werror -Wno-subobject-linkage \
    $flags $variant_flags -I board/autonomy -I "$out" -isystem "$dlib" \
    board/autonomy/tests/ert_lifecycle_test.cpp "$dlib/dlib/test_for_odr_violations.cpp" \
    $objects -lpthread -lm -o "$out/ert_lifecycle_test"
"$out/ert_lifecycle_test" "$model" "$fixture"
