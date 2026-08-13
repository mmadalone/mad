//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  test_main.cpp  (deck-patches)
//
//  doctest's entry point, in a translation unit of its own so the 9,000-line header
//  is compiled once instead of once per test file.
//
//  Run:  cmake -DMAD_TESTS=on . && make -j4 mad-tests && ./tests/mad-tests
//

#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include "doctest/doctest.h"
