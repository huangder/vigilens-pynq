#!/usr/bin/env bash
# Independent directories only; no hardware control. Run in WSL Ubuntu.
set -euo pipefail
mode=${1:?host or arm}
source_dir=${2:?official MNN 3.6.1 source root}
build_dir=${3:?new independent build directory}
cmake_bin=${VG_CMAKE:-/root/vigilens/buildroot/output/host/bin/cmake}
probe_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
args=(-S "$probe_dir" -B "$build_dir" -DCMAKE_BUILD_TYPE=Release -DMNN_SOURCE_DIR="$source_dir")
if [[ $mode == host ]]; then
  protobuf=${VG_PROTOBUF:-/root/vigilens/autonomy-20261030/protobuf-install}
  args+=(-DVG_MNN_CONVERTER=ON -DProtobuf_PROTOC_EXECUTABLE="$protobuf/bin/protoc"
         -DProtobuf_INCLUDE_DIR="$protobuf/include" -DProtobuf_LIBRARY="$protobuf/lib/libprotobuf.a")
elif [[ $mode == arm ]]; then
  export VG_ARM_TOOLCHAIN=${VG_ARM_TOOLCHAIN:-/root/vigilens/autonomy-20261030/gcc-arm-8.3-2019.03-x86_64-arm-linux-gnueabihf/bin}
  args+=(-DVG_MNN_CONVERTER=OFF -DCMAKE_TOOLCHAIN_FILE="$probe_dir/../armv7.cmake")
else
  echo 'mode must be host or arm' >&2
  exit 2
fi
"$cmake_bin" "${args[@]}"
targets=(vg_mnn_probe)
if [[ $mode == host ]]; then targets+=(MNNConvert audit_mnn_model); fi
"$cmake_bin" --build "$build_dir" --target "${targets[@]}" --parallel "${VG_JOBS:-4}"
