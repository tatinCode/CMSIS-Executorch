# groups.cmake

# group Coexistence
add_library(Group_Coexistence OBJECT
  "${SOLUTION_ROOT}/../../../firmware/vendor/alif_viewfinder/viewfinder/camera/isp_calibration.c"
  "${SOLUTION_ROOT}/../../../firmware/vendor/alif_viewfinder/viewfinder/camera/isp_calibration_mt9m114.c"
  "${SOLUTION_ROOT}/../../../firmware/asl_live/src/main.cpp"
  "${SOLUTION_ROOT}/../../../firmware/camera_inference/src/power.cpp"
  "${SOLUTION_ROOT}/generated/preview.cpp"
  "${SOLUTION_ROOT}/../../../firmware/asl_live/src/model_runner.cpp"
  "${SOLUTION_ROOT}/generated/platform.cpp"
)
target_include_directories(Group_Coexistence PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
)
target_compile_definitions(Group_Coexistence PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
add_library(Group_Coexistence_ABSTRACTIONS INTERFACE)
target_link_libraries(Group_Coexistence_ABSTRACTIONS INTERFACE
  ${CONTEXT}_ABSTRACTIONS
)
target_compile_options(Group_Coexistence PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(Group_Coexistence PUBLIC
  Group_Coexistence_ABSTRACTIONS
)
