cmake_minimum_required(VERSION 3.27)

# Roots
include("../roots.cmake")

set(CONTEXT camera-inference.debug+DevKit-E8-HP)
set(TARGET ${CONTEXT})
set(DNAME AE822FA0E5597LS0)
set(PNAME M55_HP)
set(DPACK AlifSemiconductor::Ensemble@2.2.0)
set(DPACK_DIR "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0")
set(OUT_DIR "${SOLUTION_ROOT}/out/camera-inference/DevKit-E8-HP/debug")
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_COMPILE_COMMANDS ${CMAKE_CURRENT_BINARY_DIR}/compile_commands.json)
set(COMPILE_COMMANDS ${OUT_DIR}/compile_commands.json)
set(COMPILE_MACROS_CXX ${OUT_DIR}/compile_macros_cxx.h)
set(COMPILE_MACROS_C ${OUT_DIR}/compile_macros_c.h)
set(BIN_FILE "camera-inference.bin")
set(LD_MAP_FILE "camera-inference.elf.map")
set(LD_SCRIPT "${SOLUTION_ROOT}/RTE/Device/AE822FA0E5597LS0_M55_HP/linker_gnu_mram.ld.src")
set(LD_SCRIPT_PP "${CMAKE_CURRENT_BINARY_DIR}/linker_gnu_mram.ld")

# Processor Options
set(CPU Cortex-M55)
set(FPU DP_FPU)
set(DSP DSP)
set(MVE FP_MVE)
set(BYTE_ORDER Little-endian)

# Preprocessor options
set(CPP_OPTIONS_CXX "-xc++" "-std=c++17" "-fshort-enums" "-fno-exceptions" "-fno-rtti" "-fno-unwind-tables" "-fdata-sections" "-ffunction-sections" "-Wall" "-Wextra" "-Werror")
set(CPP_OPTIONS_C "-xc" "-std=c99" "-fshort-enums" "-fdata-sections" "-ffunction-sections")

# Toolchain config map
set(COMPILER GCC)
include("toolchain.cmake")

# Setup project
project(${CONTEXT} LANGUAGES CXX C)

# Enable color diagnostics
set(CMAKE_COLOR_DIAGNOSTICS ON)

# Compilation database
add_custom_target(database DEPENDS ${COMPILE_COMMANDS} ${COMPILE_MACROS_CXX} ${COMPILE_MACROS_C})
add_custom_command(OUTPUT ${COMPILE_COMMANDS}
  COMMAND ${CMAKE_COMMAND} -E copy_if_different "${CMAKE_COMPILE_COMMANDS}" "${COMPILE_COMMANDS}"
  DEPENDS "${CMAKE_COMPILE_COMMANDS}"
)
add_custom_command(OUTPUT ${COMPILE_MACROS_CXX} ${COMPILE_MACROS_C}
  COMMAND ${CPP} ${CPP_OPTIONS_CXX} ${CPP_DUMP_MACROS} "${COMPILE_MACROS_CXX}"
  COMMAND ${CPP} ${CPP_OPTIONS_C} ${CPP_DUMP_MACROS} "${COMPILE_MACROS_C}"
)
set(CMAKE_CXX_STANDARD_INCLUDE_DIRECTORIES ${CMAKE_CXX_IMPLICIT_INCLUDE_DIRECTORIES})
set(CMAKE_C_STANDARD_INCLUDE_DIRECTORIES ${CMAKE_C_IMPLICIT_INCLUDE_DIRECTORIES})

# Setup context
add_executable(${CONTEXT})
set_target_properties(${CONTEXT} PROPERTIES PREFIX "" SUFFIX ".elf" OUTPUT_NAME "camera-inference")
set_target_properties(${CONTEXT} PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${OUT_DIR})
add_library(${CONTEXT}_GLOBAL INTERFACE)

# Includes
target_include_directories(${CONTEXT} PUBLIC
  $<$<COMPILE_LANGUAGE:C,CXX>:
    "${SOLUTION_ROOT}/../../../firmware/camera_inference/include"
    "${SOLUTION_ROOT}/../../../firmware/smoke/include"
    "${SOLUTION_ROOT}/../../../firmware/stored_image/include"
    "${SOLUTION_ROOT}/assets"
    "${SOLUTION_ROOT}/../../../firmware/vendor/alif_viewfinder/viewfinder/camera"
    "${SOLUTION_ROOT}/../runtime-cortex-m55/include"
    "${SOLUTION_ROOT}/../runtime-cortex-m55/include/executorch/runtime/core/portable_type/c10"
    "${SOLUTION_ROOT}/../../../firmware/asl_live/include"
    "${SOLUTION_ROOT}/../../../firmware/camera_overlay/include"
  >
  "${SOLUTION_ROOT}/RTE/_debug_DevKit-E8-HP"
  "${SOLUTION_ROOT}/RTE/BSP/AE822FA0E5597LS0_M55_HP"
  "${SOLUTION_ROOT}/RTE/Device/AE822FA0E5597LS0_M55_HP"
  "${CMSIS_PACK_ROOT}/ARM/CMSIS/6.1.0/CMSIS/Core/Include"
  "${CMSIS_PACK_ROOT}/ARM/CMSIS/6.1.0/CMSIS/Driver/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/soc/AE822FA0E5597/include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/soc/AE822FA0E5597/include/rtss_hp"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/system/include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/board_config"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/isp"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/isp/inc"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/port/include"
)

# Defines
target_compile_definitions(${CONTEXT} PUBLIC
  $<$<COMPILE_LANGUAGE:C,CXX>:
    C10_USING_CUSTOM_GENERATED_MACROS
    ETHOSU85
    ENSEMBLE_SOC_GEN2
    RTSS_HP
    _RTE_
  >
)

# Compile options
target_compile_options(${CONTEXT} PUBLIC
  $<$<COMPILE_LANGUAGE:C>:
    "SHELL:${CC_CPU}"
    "SHELL:${CC_FLAGS}"
    "SHELL:${CC_BYTE_ORDER}"
    "SHELL:-std=c99"
    "SHELL:-fshort-enums"
    "SHELL:-fdata-sections"
    "SHELL:-ffunction-sections"
  >
  $<$<COMPILE_LANGUAGE:CXX>:
    "SHELL:${CXX_CPU}"
    "SHELL:${CXX_FLAGS}"
    "SHELL:${CXX_BYTE_ORDER}"
    "SHELL:-std=c++17"
    "SHELL:-fshort-enums"
    "SHELL:-fno-exceptions"
    "SHELL:-fno-rtti"
    "SHELL:-fno-unwind-tables"
    "SHELL:-fdata-sections"
    "SHELL:-ffunction-sections"
    "SHELL:-Wall"
    "SHELL:-Wextra"
    "SHELL:-Werror"
  >
)

# Compile Options Abstractions
add_library(${CONTEXT}_ABSTRACTIONS INTERFACE)
cbuild_set_options_flags(CXX "balanced" "on" "" "" CXX_OPTIONS_FLAGS_${CONTEXT})
cbuild_set_options_flags(CC "balanced" "on" "" "" CC_OPTIONS_FLAGS_${CONTEXT})
target_compile_options(${CONTEXT}_ABSTRACTIONS INTERFACE
  $<$<COMPILE_LANGUAGE:CXX>:
    "SHELL:${CXX_OPTIONS_FLAGS_${CONTEXT}}"
  >
  $<$<COMPILE_LANGUAGE:C>:
    "SHELL:${CC_OPTIONS_FLAGS_${CONTEXT}}"
  >
)

# Add groups and components
include("groups.cmake")
include("components.cmake")

target_link_libraries(${CONTEXT} PUBLIC
  Group_Coexistence
  ARM_CMSIS_CORE_6_1_0
  AlifSemiconductor_BSP_Board_Config_2_2_0
  AlifSemiconductor_BSP_DevKit_Config_DevKit-e8_2_2_0
  AlifSemiconductor_BSP_External_peripherals_CAMERA_Sensor_MT9M114_2_2_0
  AlifSemiconductor_BSP_External_peripherals_ILI9806E_LCD_panel_2_2_0
  AlifSemiconductor_CMSIS_Driver_I2C_2_2_0
  AlifSemiconductor_CMSIS_Driver_USART_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_CDC_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_CPI_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_GPIO_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_ISP_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_MHU_1_110_0
  AlifSemiconductor_Device_SOC_Peripherals_MIPI_CSI2_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_CSI2_DPHY_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_2_2_0
  AlifSemiconductor_Device_SOC_Peripherals_PINCONF_2_2_0
  AlifSemiconductor_Device_Startup_2_2_0
  AlifSemiconductor_Services_Secure_Enclave_Initialization_Helper_Source_1_110_0
  AlifSemiconductor_Services_Secure_Enclave_core_Source_1_110_0
  -Wl,--start-group
    -lm
    -lc
    -lgcc
    ${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/isp/libisp_gcc.a
  -Wl,--end-group
)

# Linker options
target_link_options(${CONTEXT} PUBLIC
  "SHELL:${LD_CPU}"
  "SHELL:${_LS}\"${LD_SCRIPT_PP}\""
  "SHELL:${LD_MAP}"
  "SHELL:-Wl,--start-group"
  "SHELL:-Wl,--whole-archive"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libexecutorch.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libexecutorch_core.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libexecutorch_delegate_ethos_u.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libcortex_m_ops_lib.a"
  "SHELL:-Wl,--no-whole-archive"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libcortex_m_kernels.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libportable_ops_lib.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libportable_kernels.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libquantized_ops_lib.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libquantized_kernels.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libkernels_util_all_deps.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libcmsis-nn.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libflatccrt.a"
  "SHELL:/home/tatin/projects/hackathon/HandGPT/.work/inference/runtime-cortex-m55/lib/libethosu_core_driver.a"
  "SHELL:-Wl,--end-group"
  "SHELL:--specs=nosys.specs"
  "SHELL:--entry=Reset_Handler"
  "SHELL:-Wl,-Map=/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/camera-inference.map,--cref,--print-memory-usage,--gc-sections,--no-warn-rwx-segments"
)
set(LD_DEPS ${LD_SCRIPT})
set_target_properties(${CONTEXT} PROPERTIES LINK_DEPENDS "${LD_DEPS}")

# Linker script pre-processing
add_custom_command(TARGET ${CONTEXT} PRE_LINK COMMAND ${CPP} ARGS ${CPP_ARGS_LD_SCRIPT} BYPRODUCTS ${LD_SCRIPT_PP})

# Bin Conversion
add_custom_command(TARGET ${CONTEXT} POST_BUILD COMMAND ${CMAKE_OBJCOPY} ${ELF2BIN})
