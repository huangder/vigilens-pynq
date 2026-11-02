#!/bin/sh
# Install a reviewed LOCAL deployment package to the board's RAM directory.
# Usage: sh install_native.sh http://PC-IP:PORT/package /tmp/vigilens_autonomy
set -eu
base=${1:?local package URL required}
directory=${2:?destination directory required}
mkdir -p "$directory"
cd "$directory"
for name in vigi_camera_autonomy vg_vision_replay_arm vg_metrics_http libgomp.so.1 det1.param det1.bin det2.param det2.bin det3.param det3.bin display.html run_native.sh install_native.sh runtime.env manifest.sha256 package.json; do
    wget -q -T 30 -O "$name" "$base/$name"
done
runtime=$(sed -n 's/^VG_RUNTIME=//p' runtime.env)
case "$runtime" in
  ert_hybrid|ert_temporal) landmark=shape_predictor_68_face_landmarks.dat;;
  native|'') landmark=peppa_pruned.mnn;;
  *) echo 'Unsupported deployment runtime' >&2; exit 2;;
esac
wget -q -T 60 -O "$landmark" "$base/$landmark"
sha256sum -c manifest.sha256
chmod 755 vigi_camera_autonomy vg_vision_replay_arm vg_metrics_http run_native.sh
printf 'NATIVE_INSTALL_PASS directory=%s\n' "$directory"
