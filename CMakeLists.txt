cmake_minimum_required(VERSION 3.16)
project(surgical_video_enhancement VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(OpenCV REQUIRED COMPONENTS core imgproc imgcodecs videoio)

add_library(sve STATIC src/pipeline.cpp src/stages.cpp)
target_include_directories(sve PUBLIC include)
target_link_libraries(sve PUBLIC ${OpenCV_LIBS})
target_compile_options(sve PRIVATE $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra -Wpedantic -Werror>)

add_executable(sve_cli apps/sve_cli.cpp)
target_link_libraries(sve_cli PRIVATE sve)
target_compile_options(sve_cli PRIVATE $<$<CXX_COMPILER_ID:GNU,Clang>:-Wall -Wextra>)
