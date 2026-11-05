cmake_minimum_required(VERSION 3.28)
project(behave_rtl_frontend LANGUAGES CXX)

include(FetchContent)
find_program(PATCH_EXECUTABLE patch REQUIRED)
set(SLANG_INCLUDE_TOOLS OFF CACHE BOOL "" FORCE)
set(SLANG_INCLUDE_TESTS OFF CACHE BOOL "" FORCE)
set(SLANG_INCLUDE_INSTALL OFF CACHE BOOL "" FORCE)
set(SLANG_USE_MIMALLOC OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
FetchContent_Declare(slang
  URL https://codeload.github.com/MikePopoloski/slang/tar.gz/8acc660a2
  URL_HASH SHA256=b0cb1b49d10f400e6c98cc4b26affbd9af4b9e5f2dffd53754b773139c9f6386
  PATCH_COMMAND ${PATCH_EXECUTABLE} -p1 --batch --forward -i ${CMAKE_CURRENT_SOURCE_DIR}/slang_conversion_kind.patch
    COMMAND ${PATCH_EXECUTABLE} -p1 --batch --forward -i ${CMAKE_CURRENT_SOURCE_DIR}/slang_comb_task_dependencies.patch
    COMMAND ${PATCH_EXECUTABLE} -p1 --batch --forward -i ${CMAKE_CURRENT_SOURCE_DIR}/slang_dataflow_read_hook.patch
    COMMAND ${PATCH_EXECUTABLE} -p1 --batch --forward -i ${CMAKE_CURRENT_SOURCE_DIR}/slang_atomic_loop_initializers.patch
    COMMAND ${PATCH_EXECUTABLE} -p1 --batch --forward -i ${CMAKE_CURRENT_SOURCE_DIR}/slang_pattern_elements.patch
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(slang)
add_executable(behave-rtl-frontend main.cpp)
target_compile_features(behave-rtl-frontend PRIVATE cxx_std_20)
target_link_libraries(behave-rtl-frontend PRIVATE slang::slang)
install(TARGETS behave-rtl-frontend RUNTIME DESTINATION libexec)
