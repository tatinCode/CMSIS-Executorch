cmake_minimum_required(VERSION 3.27)
include(ExternalProject)
	
project("camera-inference" NONE)

# Enable color diagnostics
set(CMAKE_COLOR_DIAGNOSTICS ON)

# Roots
include("roots.cmake")

# Context specific lists
set(CONTEXTS
  "camera-inference.debug+DevKit-E8-HP"
)
list(LENGTH CONTEXTS CONTEXTS_LENGTH)
math(EXPR CONTEXTS_LENGTH "${CONTEXTS_LENGTH}-1")

set(COMPILERS
  "GCC V13.3.1"
)

set(DIRS
  "${CMAKE_CURRENT_SOURCE_DIR}/camera-inference.debug+DevKit-E8-HP"
)

set(OUTPUTS_1
  "${SOLUTION_ROOT}/out/camera-inference/DevKit-E8-HP/debug/camera-inference.bin"
  "${SOLUTION_ROOT}/out/camera-inference/DevKit-E8-HP/debug/camera-inference.elf"
  "${SOLUTION_ROOT}/out/camera-inference/DevKit-E8-HP/debug/camera-inference.elf.map"
  "${SOLUTION_ROOT}/out/camera-inference/DevKit-E8-HP/debug/compile_commands.json"
  "${SOLUTION_ROOT}/out/camera-inference/DevKit-E8-HP/debug/compile_macros_c.h"
  "${SOLUTION_ROOT}/out/camera-inference/DevKit-E8-HP/debug/compile_macros_cxx.h"
)

set(ARGS
  "-DSOLUTION_ROOT=${SOLUTION_ROOT}"
  "-DCMSIS_PACK_ROOT=${CMSIS_PACK_ROOT}"
  "-DCMSIS_COMPILER_ROOT=${CMSIS_COMPILER_ROOT}"
)

# Compilation database
add_custom_target(database)

# Iterate over contexts
foreach(INDEX RANGE ${CONTEXTS_LENGTH})

  math(EXPR N "${INDEX}+1")
  list(GET CONTEXTS ${INDEX} CONTEXT)
  list(GET COMPILERS ${INDEX} COMPILER)
  list(GET DIRS ${INDEX} DIR)

  # Create external project, set configure and build steps
  ExternalProject_Add(${CONTEXT}
    PREFIX                ${DIR}
    SOURCE_DIR            ${DIR}
    BINARY_DIR            ${N}
    INSTALL_COMMAND       ""
    TEST_COMMAND          ""
    CONFIGURE_COMMAND     ${CMAKE_COMMAND} -G Ninja -S <SOURCE_DIR> -B <BINARY_DIR> ${ARGS} 
    BUILD_COMMAND         ${CMAKE_COMMAND} -E cmake_echo_color --blue --bold "Building CMake target '${CONTEXT}'"
    COMMAND               ${CMAKE_COMMAND} -E echo "Using compiler: ${COMPILER}"
    COMMAND               ${CMAKE_COMMAND} --build <BINARY_DIR>
    BUILD_ALWAYS          TRUE
    BUILD_BYPRODUCTS      ${OUTPUTS_${N}}
    LOG_CONFIGURE         ON
    LOG_OUTPUT_ON_FAILURE ON
    USES_TERMINAL_BUILD   ON
  )

  # Executes command step
  ExternalProject_Add_Step(${CONTEXT} executes
    DEPENDEES         build
  )

  ExternalProject_Add_StepTargets(${CONTEXT} build configure executes)

  # Debug
  message(VERBOSE "Configure Context: ${CMAKE_COMMAND} -G Ninja -S ${DIR} -B ${N}")

  # Database generation step
  ExternalProject_Add_Step(${CONTEXT} database
    COMMAND           ${CMAKE_COMMAND} --build <BINARY_DIR> --target database
    ALWAYS            TRUE
    LOG               TRUE
    USES_TERMINAL     ON
    DEPENDEES         configure
  )
  ExternalProject_Add_StepTargets(${CONTEXT} database)
  add_dependencies(database ${CONTEXT}-database)

endforeach()
