#  SPDX-License-Identifier: MIT
#
#  ES-DE Frontend
#  tests/CMakeLists.txt  (deck-patches)
#
#  The MAD control panel's unit tests. Opt-in and EXCLUDE_FROM_ALL, so the AppImage
#  recipe is unaffected: it reconfigures from scratch without MAD_TESTS and never
#  installs this target.
#
#  Deliberately does NOT link es-core. Everything tested here is pure logic that
#  needs at most rapidjson, which keeps the binary a couple of seconds to build and
#  means a test run cannot be broken by SDL, GL or FFmpeg. That is also the reason
#  the suite covers what it covers: a MAD page cannot be constructed without a live
#  font and renderer (MadPage's ctor builds a TextComponent), so the tested surface
#  is the logic that has been lifted OUT of the pages, never the pages themselves.
#

set(MAD_TEST_SOURCES
    ${CMAKE_CURRENT_SOURCE_DIR}/test_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/test_size_format.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/test_json_helpers.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/test_backup_row_map.cpp
)

add_executable(mad-tests ${MAD_TEST_SOURCES})

target_include_directories(mad-tests PRIVATE
                           ${CMAKE_CURRENT_SOURCE_DIR}
                           ${CMAKE_SOURCE_DIR}/es-app/src
                           ${COMMON_INCLUDE_DIRS})

# -UNDEBUG is load-bearing, not cosmetic: no CMAKE_BUILD_TYPE is passed by the
# AppImage recipe or by a plain `cmake .`, so the release branch applies and adds
# -DNDEBUG. doctest's REQUIRE/CHECK do not use assert(), but anything that later
# reaches for one would compile to nothing and pass silently without this.
target_compile_options(mad-tests PRIVATE -UNDEBUG)

# Keep the binary out of the repo root, where the top-level CMakeLists forces every
# other executable (EXECUTABLE_OUTPUT_PATH) and where it would sit beside es-de.
set_target_properties(mad-tests PROPERTIES RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})

add_test(NAME mad-tests COMMAND mad-tests)
