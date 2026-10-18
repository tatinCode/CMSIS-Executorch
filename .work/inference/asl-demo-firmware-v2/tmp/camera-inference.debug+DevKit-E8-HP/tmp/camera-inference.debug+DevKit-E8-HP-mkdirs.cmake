# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file Copyright.txt or https://cmake.org/licensing for details.

cmake_minimum_required(VERSION ${CMAKE_VERSION}) # this file comes with cmake

# If CMAKE_DISABLE_SOURCE_CHANGES is set to true and the source directory is an
# existing directory in our source tree, calling file(MAKE_DIRECTORY) on it
# would cause a fatal error, even though it would be a no-op.
if(NOT EXISTS "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP")
  file(MAKE_DIRECTORY "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP")
endif()
file(MAKE_DIRECTORY
  "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/1"
  "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP"
  "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP/tmp"
  "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP/src/camera-inference.debug+DevKit-E8-HP-stamp"
  "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP/src"
  "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP/src/camera-inference.debug+DevKit-E8-HP-stamp"
)

set(configSubDirs )
foreach(subDir IN LISTS configSubDirs)
    file(MAKE_DIRECTORY "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP/src/camera-inference.debug+DevKit-E8-HP-stamp/${subDir}")
endforeach()
if(cfgdir)
  file(MAKE_DIRECTORY "/home/tatin/projects/hackathon/HandGPT/.work/inference/asl-demo-firmware-v2/tmp/camera-inference.debug+DevKit-E8-HP/src/camera-inference.debug+DevKit-E8-HP-stamp${cfgdir}") # cfgdir has leading slash
endif()
