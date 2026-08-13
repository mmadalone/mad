//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  test_size_format.cpp  (deck-patches)
//
//  MadPageUtil::humanSize / humanSizeCompact render the same byte count two ways,
//  and both rules are load-bearing for layout as well as for meaning.
//
//  Why this file exists: the 2026-08-12 audit found this formatter in TEN local
//  copies across four different output styles, so the same size read differently
//  depending on which page showed it. Phase 4b collapsed them onto these two, and
//  the compact rule then had to be corrected during review - the first attempt
//  rounded the whole kilobyte band, which rendered 1.5K and 2.4K identically as
//  "2K" on exactly the pages that list config files. These cases pin both rules,
//  including the boundaries where the branches change.
//
//  Run:  cmake -DMAD_TESTS=on . && make -j4 mad-tests && ./tests/mad-tests -ts=size-format
//

#include "doctest/doctest.h"

#include "guis/mad/MadPageUtil.h"

TEST_SUITE("size-format")
{
    TEST_CASE("humanSize spells out the unit with a space")
    {
        CHECK(MadPageUtil::humanSize(0) == "0 B");
        CHECK(MadPageUtil::humanSize(1) == "1 B");
        CHECK(MadPageUtil::humanSize(1023) == "1023 B");
        CHECK(MadPageUtil::humanSize(1024) == "1.0 KB");
        CHECK(MadPageUtil::humanSize(1536) == "1.5 KB");
        CHECK(MadPageUtil::humanSize(1024LL * 1024) == "1.0 MB");
        CHECK(MadPageUtil::humanSize(1024LL * 1024 * 1024) == "1.0 GB");
        CHECK(MadPageUtil::humanSize(1024LL * 1024 * 1024 * 1024) == "1.0 TB");
    }

    TEST_CASE("humanSize keeps one decimal at every magnitude")
    {
        // The long form is used on roomy surfaces (the two "back up ALL" confirms and
        // the full-width file lists), so it never drops the decimal to save room.
        CHECK(MadPageUtil::humanSize(12LL * 1024 * 1024) == "12.0 MB");
        CHECK(MadPageUtil::humanSize(999LL * 1024 * 1024) == "999.0 MB");
    }

    TEST_CASE("humanSize stops at TB rather than inventing a unit")
    {
        // The unit table ends at TB; a petabyte-scale number must still render, not
        // walk off the end of the array.
        const std::string huge {MadPageUtil::humanSize(1024LL * 1024 * 1024 * 1024 * 4096)};
        CHECK(huge.find(" TB") != std::string::npos);
    }

    TEST_CASE("humanSizeCompact drops the space and the second unit letter")
    {
        // For chips, tally lines and the 60%-width group rows, where three extra
        // glyphs cost real width.
        CHECK(MadPageUtil::humanSizeCompact(0) == "0B");
        CHECK(MadPageUtil::humanSizeCompact(512) == "512B");
        CHECK(MadPageUtil::humanSizeCompact(1023) == "1023B");
        CHECK(MadPageUtil::humanSizeCompact(1024LL * 1024 * 1024) == "1.0G");
    }

    TEST_CASE("humanSizeCompact keeps a decimal only below ten")
    {
        // THE regression this file exists for. Config groups are mostly kilobytes, so
        // rounding that band to whole units would render two different sizes the same.
        CHECK(MadPageUtil::humanSizeCompact(1536) == "1.5K");   // must NOT be "2K"
        CHECK(MadPageUtil::humanSizeCompact(2458) == "2.4K");   // must NOT also be "2K"
        CHECK(MadPageUtil::humanSizeCompact(1536) != MadPageUtil::humanSizeCompact(2458));

        // ...and above ten the decimal is noise, so it goes.
        CHECK(MadPageUtil::humanSizeCompact(10240) == "10K");
        CHECK(MadPageUtil::humanSizeCompact(524288) == "512K");
    }

    TEST_CASE("humanSizeCompact switches unit exactly at 1024, not before")
    {
        CHECK(MadPageUtil::humanSizeCompact(1023) == "1023B");
        CHECK(MadPageUtil::humanSizeCompact(1024) == "1.0K");
        CHECK(MadPageUtil::humanSizeCompact(1024LL * 1024 - 1) == "1024K");
        CHECK(MadPageUtil::humanSizeCompact(1024LL * 1024) == "1.0M");
    }

    TEST_CASE("a negative size does not render as a huge one")
    {
        // Sizes arrive from the daemon as JSON ints; a missing field reads back as 0,
        // but a negative must not wrap into an enormous unsigned-looking number.
        const std::string compact {MadPageUtil::humanSizeCompact(-1)};
        CHECK(compact.find('T') == std::string::npos);
        CHECK(compact.find('G') == std::string::npos);
    }
}
