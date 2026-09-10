# C++ execution engine — architecture.md §7, milestone M4.
#
# Three targets, in the order architecture.md requires them to be built and
# trusted: the core library, its own unit tests, and only then the Python
# binding. The binding is optional (`-DQUANT_BUILD_PYTHON_MODULE=OFF`) so the
# C++ side can be built and tested with no Python toolchain at all.

cmake_minimum_required(VERSION 3.24)
project(quant_execution LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  # The benchmark in M4.6 is meaningless against an unoptimised build, and a
  # debug build is the easiest way to publish an accidentally dishonest number.
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(QUANT_BUILD_TESTS "Build the C++ unit tests" ON)
option(QUANT_BUILD_PYTHON_MODULE "Build the pybind11 extension" ON)

# ---------------------------------------------------------------------------
# Floating-point policy.
#
# The parity test asserts that this engine and the Python engine agree to 1e-12.
# That only holds if the compiler performs the same operations in the same
# order, so:
#   * -ffp-contract=off  — clang defaults to fusing `a*b+c` into an FMA, which
#     keeps an extra-precision intermediate and produces a *different* (in fact
#     slightly more accurate) result than CPython's separate multiply and add.
#   * no -ffast-math / -Ofast — those licence reassociation, which would break
#     parity in ways that only show up on some inputs.
# This is a correctness constraint, not a conservatism: the fast variants are
# available and are deliberately not used.
# ---------------------------------------------------------------------------
add_library(quant_execution_flags INTERFACE)
target_compile_options(quant_execution_flags INTERFACE
  $<$<COMPILE_LANG_AND_ID:CXX,AppleClang,Clang,GNU>:-ffp-contract=off>
  $<$<COMPILE_LANG_AND_ID:CXX,AppleClang,Clang,GNU>:-Wall>
  $<$<COMPILE_LANG_AND_ID:CXX,AppleClang,Clang,GNU>:-Wextra>
  $<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/fp:precise>
)

add_library(quant_execution_core STATIC
  src/portfolio.cpp
  src/simulator.cpp
)
target_include_directories(quant_execution_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_link_libraries(quant_execution_core PUBLIC quant_execution_flags)
set_target_properties(quant_execution_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

# ------------------------------------------------------------------- tests ---
if(QUANT_BUILD_TESTS)
  include(FetchContent)
  # FIND_PACKAGE_ARGS first: a system/brew Catch2 is used when present, so a
  # clean build on a machine with no network is still possible.
  FetchContent_Declare(
    Catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2.git
    GIT_TAG v3.7.1
    FIND_PACKAGE_ARGS 3 NAMES Catch2
  )
  FetchContent_MakeAvailable(Catch2)

  add_executable(quant_execution_tests
    tests/test_events.cpp
    tests/test_portfolio.cpp
    tests/test_simulator.cpp
  )
  target_link_libraries(quant_execution_tests PRIVATE quant_execution_core Catch2::Catch2WithMain)

  enable_testing()
  list(APPEND CMAKE_MODULE_PATH ${Catch2_SOURCE_DIR}/extras)
  include(CTest)
  include(Catch)
  catch_discover_tests(quant_execution_tests)
endif()

# ------------------------------------------------------------------ binding ---
if(QUANT_BUILD_PYTHON_MODULE)
  find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)
  if(NOT pybind11_DIR)
    # Ask the interpreter that will import the module where its pybind11 is,
    # rather than hoping a system-wide copy matches.
    execute_process(
      COMMAND ${Python3_EXECUTABLE} -c "import pybind11; print(pybind11.get_cmake_dir())"
      OUTPUT_VARIABLE pybind11_DIR
      OUTPUT_STRIP_TRAILING_WHITESPACE
      RESULT_VARIABLE _pybind11_probe
    )
    if(NOT _pybind11_probe EQUAL 0)
      message(FATAL_ERROR
        "pybind11 is not importable by ${Python3_EXECUTABLE}. "
        "Install it with `uv pip install pybind11` (it is a build-time dependency only).")
    endif()
  endif()
  find_package(pybind11 CONFIG REQUIRED)

  pybind11_add_module(quant_execution MODULE bindings/module.cpp)
  target_link_libraries(quant_execution PRIVATE quant_execution_core)
  # Land the extension directly in the build directory the Python wrapper
  # searches, so `make build-cpp` is the whole story and nothing has to be
  # copied into the source tree.
  set_target_properties(quant_execution PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}
  )
endif()
