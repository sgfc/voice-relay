cmake_minimum_required(VERSION 3.25)

# CMake must see the target architecture before it enables a compiler. Keep
# this host-only default scoped to native macOS configuration.
if(CMAKE_HOST_SYSTEM_NAME STREQUAL "Darwin")
  if(NOT CMAKE_OSX_ARCHITECTURES)
    set(CMAKE_OSX_ARCHITECTURES "arm64" CACHE STRING "macOS host architecture")
  endif()
  if(NOT CMAKE_OSX_DEPLOYMENT_TARGET)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "13.0" CACHE STRING "Minimum macOS version")
  endif()
endif()

project(charadock_beatrice_host LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(WIN32)
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

if(APPLE)
  enable_language(OBJCXX)
endif()

set(VST3_SDK_ROOT "" CACHE PATH "Path to the Steinberg VST3 SDK")
if(NOT EXISTS "${VST3_SDK_ROOT}/CMakeLists.txt")
  message(FATAL_ERROR "Set VST3_SDK_ROOT to the Steinberg VST3 SDK checkout")
endif()

set(SMTG_ENABLE_VST3_PLUGIN_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_HOSTING_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VSTGUI_SUPPORT OFF CACHE BOOL "" FORCE)
add_subdirectory("${VST3_SDK_ROOT}" vst3sdk EXCLUDE_FROM_ALL)

if(WIN32)
  set(CHARADOCK_VST3_MODULE_SOURCE
    "${VST3_SDK_ROOT}/public.sdk/source/vst/hosting/module_win32.cpp"
  )
elseif(APPLE)
  set(CHARADOCK_VST3_MODULE_SOURCE
    "${VST3_SDK_ROOT}/public.sdk/source/vst/hosting/module_mac.mm"
  )
  set_source_files_properties(
    "${CHARADOCK_VST3_MODULE_SOURCE}"
    PROPERTIES COMPILE_FLAGS "-fobjc-arc"
  )
else()
  message(FATAL_ERROR "The CharaDock Beatrice host currently supports Windows and macOS only")
endif()

add_executable(charadock-beatrice-host
  src/main.cpp
  "${VST3_SDK_ROOT}/public.sdk/source/common/memorystream.cpp"
  "${CHARADOCK_VST3_MODULE_SOURCE}"
  "${VST3_SDK_ROOT}/public.sdk/source/vst/hosting/plugprovider.cpp"
)
target_include_directories(charadock-beatrice-host PRIVATE "${VST3_SDK_ROOT}")
target_link_libraries(charadock-beatrice-host PRIVATE sdk_hosting)
if(MSVC)
  target_compile_options(charadock-beatrice-host PRIVATE /utf-8 /W4 /Zc:char8_t-)
else()
  target_compile_options(charadock-beatrice-host PRIVATE -Wall -Wextra -Wpedantic)
endif()
if(MINGW)
  target_link_options(charadock-beatrice-host PRIVATE -municode -static -static-libgcc -static-libstdc++)
endif()
if(APPLE)
  find_library(COCOA_FRAMEWORK Cocoa REQUIRED)
  target_link_libraries(charadock-beatrice-host PRIVATE "${COCOA_FRAMEWORK}")
  set_target_properties(charadock-beatrice-host PROPERTIES OSX_ARCHITECTURES "${CMAKE_OSX_ARCHITECTURES}")
endif()

install(TARGETS charadock-beatrice-host RUNTIME DESTINATION .)
