cmake_minimum_required(VERSION 3.16)
project(mooncake_ascend_regression LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(MOONCAKE_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/../upstream" CACHE PATH "Pinned source")
set(TENT "${MOONCAKE_SOURCE}/mooncake-transfer-engine/tent")
set(REGRESSION_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/../upstream/mooncake-transfer-engine/tent/tests/ascend_direct_transport_test.cpp")
find_package(GTest REQUIRED)
find_path(GLOG_INCLUDE glog/logging.h REQUIRED)
find_library(GLOG_LIBRARY glog REQUIRED)
add_executable(ascend_regression
  "${REGRESSION_SOURCE}"
  "${TENT}/src/transport/ascend/ascend_direct_transport.cpp"
  "${TENT}/src/transport/ascend/hixl_engine.cpp"
  "${TENT}/src/transport/ascend/local_copy_engine.cpp"
  "${TENT}/src/transport/ascend/resource_config.cpp"
  "${TENT}/src/runtime/segment_manager.cpp"
  "${TENT}/src/runtime/slab.cpp"
  "${TENT}/src/common/config.cpp"
  "${TENT}/src/common/status.cpp"
  "${TENT}/src/runtime/topology.cpp"
  cpu_shims/control_plane.cpp)
target_include_directories(ascend_regression PRIVATE cpu_shims "${TENT}/include" "${GLOG_INCLUDE}")
target_link_libraries(ascend_regression PRIVATE GTest::gtest_main "${GLOG_LIBRARY}" pthread numa)
target_compile_options(ascend_regression PRIVATE -ffunction-sections -fdata-sections)
target_link_options(ascend_regression PRIVATE -Wl,--gc-sections)
if(SANITIZER)
  target_compile_options(ascend_regression PRIVATE "-fsanitize=${SANITIZER}" -fno-omit-frame-pointer)
  target_link_options(ascend_regression PRIVATE "-fsanitize=${SANITIZER}")
endif()
enable_testing()
add_test(NAME ascend_regression COMMAND ascend_regression)
set_tests_properties(ascend_regression PROPERTIES TIMEOUT 120)
