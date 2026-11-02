# Include after creating tensorflow-lite. Uses the official MediaPipe CPU kernels
# with attribute parsers extracted from its TensorFlow patch into the build tree.
if(NOT TARGET tensorflow-lite)
  message(FATAL_ERROR "Create tensorflow-lite before including mediapipe_ops.cmake")
endif()
foreach(required VG_MEDIAPIPE_SOURCE VG_TENSORFLOW_SOURCE)
  if(NOT DEFINED ${required} OR NOT IS_DIRECTORY "${${required}}")
    message(FATAL_ERROR "${required} must name an existing source directory")
  endif()
endforeach()
find_package(Python3 REQUIRED COMPONENTS Interpreter)
list(PREPEND CMAKE_MODULE_PATH "${VG_TENSORFLOW_SOURCE}/tensorflow/lite/tools/cmake/modules")
if(EXISTS "${FP16_SOURCE_DIR}/include/fp16.h")
  set(_vg_fp16_include "${FP16_SOURCE_DIR}/include")
else()
  find_package(fp16_headers REQUIRED)
  set(_vg_fp16_include "${fp16_headers_SOURCE_DIR}/include")
endif()
set(_vg_ops_overlay "${CMAKE_CURRENT_BINARY_DIR}/mediapipe_ops_overlay")
execute_process(
  COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_LIST_DIR}/prepare_tf_ops.py"
          --mediapipe-source "${VG_MEDIAPIPE_SOURCE}" --overlay "${_vg_ops_overlay}"
  RESULT_VARIABLE _vg_prepare_result OUTPUT_VARIABLE _vg_prepare_output
  ERROR_VARIABLE _vg_prepare_error)
if(NOT _vg_prepare_result EQUAL 0)
  message(FATAL_ERROR "MediaPipe parser extraction failed: ${_vg_prepare_error}")
endif()
set(_vg_cpu_dir "${VG_MEDIAPIPE_SOURCE}/mediapipe/util/tflite/operations")
set(_vg_ops_names landmarks_to_transform_matrix transform_landmarks transform_tensor_bilinear)
set(_vg_ops_sources)
foreach(op IN LISTS _vg_ops_names)
  list(APPEND _vg_ops_sources "${_vg_cpu_dir}/${op}.cc" "${_vg_ops_overlay}/cpu_parsers/${op}.cc")
endforeach()
add_library(vg_mediapipe_ops STATIC ${_vg_ops_sources})
# Overlay precedes TensorFlow so only missing custom-op headers come from it.
# The official CPU headers remain visible to users calling Register*V2().
target_include_directories(vg_mediapipe_ops BEFORE PUBLIC
  "${_vg_ops_overlay}" "${VG_MEDIAPIPE_SOURCE}" "${VG_TENSORFLOW_SOURCE}"
  "${_vg_fp16_include}")
target_compile_features(vg_mediapipe_ops PUBLIC cxx_std_17)
target_link_libraries(vg_mediapipe_ops PUBLIC tensorflow-lite absl::status)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${CMAKE_CURRENT_LIST_DIR}/prepare_tf_ops.py"
  "${VG_MEDIAPIPE_SOURCE}/third_party/org_tensorflow_custom_ops.diff")
