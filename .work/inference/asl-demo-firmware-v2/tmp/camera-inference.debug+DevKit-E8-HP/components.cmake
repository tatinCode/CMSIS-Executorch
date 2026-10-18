# components.cmake

# component ARM::CMSIS:CORE@6.1.0
add_library(ARM_CMSIS_CORE_6_1_0 INTERFACE)
target_include_directories(ARM_CMSIS_CORE_6_1_0 INTERFACE
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/ARM/CMSIS/6.1.0/CMSIS/Core/Include"
)
target_compile_definitions(ARM_CMSIS_CORE_6_1_0 INTERFACE
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_link_libraries(ARM_CMSIS_CORE_6_1_0 INTERFACE
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::BSP:Board Config@2.2.0
add_library(AlifSemiconductor_BSP_Board_Config_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/board_config/board_config.c"
)
target_include_directories(AlifSemiconductor_BSP_Board_Config_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/board_config"
)
target_compile_definitions(AlifSemiconductor_BSP_Board_Config_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_BSP_Board_Config_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_BSP_Board_Config_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::BSP:DevKit Config&DevKit-e8@2.2.0
add_library(AlifSemiconductor_BSP_DevKit_Config_DevKit-e8_2_2_0 INTERFACE)
target_include_directories(AlifSemiconductor_BSP_DevKit_Config_DevKit-e8_2_2_0 INTERFACE
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${SOLUTION_ROOT}/RTE/BSP/AE822FA0E5597LS0_M55_HP"
)
target_compile_definitions(AlifSemiconductor_BSP_DevKit_Config_DevKit-e8_2_2_0 INTERFACE
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_link_libraries(AlifSemiconductor_BSP_DevKit_Config_DevKit-e8_2_2_0 INTERFACE
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::BSP:External peripherals:CAMERA Sensor MT9M114@2.2.0
add_library(AlifSemiconductor_BSP_External_peripherals_CAMERA_Sensor_MT9M114_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/mt9m114_isp_param.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/components/Source/MT9M114_Camera_Sensor.c"
)
target_include_directories(AlifSemiconductor_BSP_External_peripherals_CAMERA_Sensor_MT9M114_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
)
target_compile_definitions(AlifSemiconductor_BSP_External_peripherals_CAMERA_Sensor_MT9M114_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_BSP_External_peripherals_CAMERA_Sensor_MT9M114_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_BSP_External_peripherals_CAMERA_Sensor_MT9M114_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::BSP:External peripherals:ILI9806E LCD panel@2.2.0
add_library(AlifSemiconductor_BSP_External_peripherals_ILI9806E_LCD_panel_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/components/Source/ILI9806E_LCD_panel.c"
)
target_include_directories(AlifSemiconductor_BSP_External_peripherals_ILI9806E_LCD_panel_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
)
target_compile_definitions(AlifSemiconductor_BSP_External_peripherals_ILI9806E_LCD_panel_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_BSP_External_peripherals_ILI9806E_LCD_panel_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_BSP_External_peripherals_ILI9806E_LCD_panel_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::CMSIS Driver:I2C@2.2.0
add_library(AlifSemiconductor_CMSIS_Driver_I2C_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_I2C.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/i2c.c"
)
target_include_directories(AlifSemiconductor_CMSIS_Driver_I2C_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
  "${CMSIS_PACK_ROOT}/ARM/CMSIS/6.1.0/CMSIS/Driver/Include"
)
target_compile_definitions(AlifSemiconductor_CMSIS_Driver_I2C_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_CMSIS_Driver_I2C_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_CMSIS_Driver_I2C_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::CMSIS Driver:USART@2.2.0
add_library(AlifSemiconductor_CMSIS_Driver_USART_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_USART.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/uart.c"
)
target_include_directories(AlifSemiconductor_CMSIS_Driver_USART_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
  "${CMSIS_PACK_ROOT}/ARM/CMSIS/6.1.0/CMSIS/Driver/Include"
)
target_compile_definitions(AlifSemiconductor_CMSIS_Driver_USART_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_CMSIS_Driver_USART_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_CMSIS_Driver_USART_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:CDC@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_CDC_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_CDC200.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/cdc.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_CDC_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_CDC_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_CDC_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_CDC_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:CPI@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_CPI_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Camera_Sensor_i2c.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_CPI.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/cpi.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_CPI_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_CPI_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_CPI_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_CPI_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:GPIO@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_GPIO_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_IO_Private.h"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_IO.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_GPIO_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_GPIO_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_GPIO_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_GPIO_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:ISP@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_ISP_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_ISP.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_ISP_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/isp"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/isp/inc"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_ISP_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_ISP_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_ISP_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
  ${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/libs/isp/libisp_gcc.a
)

# component AlifSemiconductor::Device:SOC Peripherals:MHU@1.110.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_MHU_1_110_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/mhu_driver.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/mhu_receiver.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/mhu_sender.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_MHU_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_MHU_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_MHU_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_MHU_1_110_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:MIPI CSI2@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_MIPI_CSI2_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/DPHY_CSI2.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_MIPI_CSI2.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/csi.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_MIPI_CSI2_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_MIPI_CSI2_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_MIPI_CSI2_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_MIPI_CSI2_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:MIPI DSI CSI2 DPHY@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_CSI2_DPHY_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/DPHY_Common.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_CSI2_DPHY_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_CSI2_DPHY_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_CSI2_DPHY_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_CSI2_DPHY_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:MIPI DSI@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/DPHY_DSI.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source/Driver_MIPI_DSI.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/dsi.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Alif_CMSIS/Source"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_MIPI_DSI_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:SOC Peripherals:PINCONF@2.2.0
add_library(AlifSemiconductor_Device_SOC_Peripherals_PINCONF_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/source/pinconf.c"
)
target_include_directories(AlifSemiconductor_Device_SOC_Peripherals_PINCONF_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/drivers/include"
)
target_compile_definitions(AlifSemiconductor_Device_SOC_Peripherals_PINCONF_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_SOC_Peripherals_PINCONF_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_SOC_Peripherals_PINCONF_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Device:Startup@2.2.0
add_library(AlifSemiconductor_Device_Startup_2_2_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/cache.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/mpu.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/pm.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/sau_tcm_ns_setup.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/startup.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/system.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/tgu.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/source/vectors.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/system/source/sys_clocks.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/system/source/sys_utils.c"
)
target_include_directories(AlifSemiconductor_Device_Startup_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${SOLUTION_ROOT}/RTE/Device/AE822FA0E5597LS0_M55_HP"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/core/common/include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/soc/AE822FA0E5597/include"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/soc/AE822FA0E5597/include/rtss_hp"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/Device/system/include"
)
target_compile_definitions(AlifSemiconductor_Device_Startup_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Device_Startup_2_2_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Device_Startup_2_2_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Services:Secure Enclave:Initialization Helper&Source@1.110.0
add_library(AlifSemiconductor_Services_Secure_Enclave_Initialization_Helper_Source_1_110_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/port/clock_runtime.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/port/se_services_port.c"
)
target_include_directories(AlifSemiconductor_Services_Secure_Enclave_Initialization_Helper_Source_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/port/include"
)
target_compile_definitions(AlifSemiconductor_Services_Secure_Enclave_Initialization_Helper_Source_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Services_Secure_Enclave_Initialization_Helper_Source_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Services_Secure_Enclave_Initialization_Helper_Source_1_110_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)

# component AlifSemiconductor::Services:Secure Enclave:core&Source@1.110.0
add_library(AlifSemiconductor_Services_Secure_Enclave_core_Source_1_110_0 OBJECT
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_application.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_boot.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_clocks.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_cryptocell.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_error.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_extsys0.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_handler.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_maintenance.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_padcontrol.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_pinmux.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_power.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_system.c"
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/source/services_host_update.c"
)
target_include_directories(AlifSemiconductor_Services_Secure_Enclave_core_Source_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_INCLUDE_DIRECTORIES>
  "${CMSIS_PACK_ROOT}/AlifSemiconductor/Ensemble/2.2.0/se_services/include"
)
target_compile_definitions(AlifSemiconductor_Services_Secure_Enclave_core_Source_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_DEFINITIONS>
)
target_compile_options(AlifSemiconductor_Services_Secure_Enclave_core_Source_1_110_0 PUBLIC
  $<TARGET_PROPERTY:${CONTEXT},INTERFACE_COMPILE_OPTIONS>
)
target_link_libraries(AlifSemiconductor_Services_Secure_Enclave_core_Source_1_110_0 PUBLIC
  ${CONTEXT}_ABSTRACTIONS
)
