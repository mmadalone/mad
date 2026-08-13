//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  test_backup_row_map.cpp  (deck-patches)
//
//  MadBackupRowMap::classifyRow decides what a press on the backup / restore list
//  means: tick a group, run the operation, or run one of the extra ops a page
//  appended below the action row.
//
//  Why this file exists: this is the hazard the phase 4b consolidation was warned
//  about by name. System config and ES-DE settings originally found the action row
//  as "any index past the groups", while Controller config used equality, because
//  restore mode puts two LOCAL revert ops UNDERNEATH it. Folding the pages onto the
//  majority form would have meant that pressing "Revert emulator inputs to pre-MAD
//  backups" ran a full restore instead - a destructive action reached by asking for
//  a different one. The rule was also written out twice (onListSelect and
//  updateExplain), so it could drift between what the list DOES and what the
//  explanation pane SAYS. There is one copy now, and these cases pin it.
//
//  Run:  cmake -DMAD_TESTS=on . && make -j4 mad-tests && ./tests/mad-tests -ts=backup-row-map
//

#include "doctest/doctest.h"

#include "guis/mad/MadBackupRowMap.h"

using Kind = MadBackupRowMap::RowKind;

TEST_SUITE("backup-row-map")
{
    TEST_CASE("groups come first and map to themselves")
    {
        CHECK(MadBackupRowMap::classifyRow(0, 5).kind == Kind::Group);
        CHECK(MadBackupRowMap::classifyRow(0, 5).index == 0);
        CHECK(MadBackupRowMap::classifyRow(4, 5).kind == Kind::Group);
        CHECK(MadBackupRowMap::classifyRow(4, 5).index == 4);
    }

    TEST_CASE("the action row is found by equality, not by being last")
    {
        // THE case. With two extra rows below it the action row is NOT the last row,
        // so it can only be identified by its exact index.
        CHECK(MadBackupRowMap::classifyRow(5, 5).kind == Kind::Action);
    }

    TEST_CASE("rows below the action row are extras, numbered from zero")
    {
        // Controller config in restore mode: 5 groups, the action row, then
        // "Revert emulator inputs" and "Reset routing overrides".
        const MadBackupRowMap::RowHit revert {MadBackupRowMap::classifyRow(6, 5)};
        const MadBackupRowMap::RowHit reset {MadBackupRowMap::classifyRow(7, 5)};
        CHECK(revert.kind == Kind::Extra);
        CHECK(revert.index == 0);
        CHECK(reset.kind == Kind::Extra);
        CHECK(reset.index == 1);
    }

    TEST_CASE("an extra row never classifies as the action row")
    {
        // The regression that matters: if this ever answers Action for an index past
        // the groups, asking to revert emulator inputs runs a restore instead.
        for (int extra {1}; extra <= 4; ++extra)
            CHECK(MadBackupRowMap::classifyRow(5 + extra, 5).kind != Kind::Action);
    }

    TEST_CASE("with no extra rows the action row is simply the last one")
    {
        // System config and ES-DE settings append nothing, so the behaviour there is
        // unchanged by the equality rule.
        CHECK(MadBackupRowMap::classifyRow(3, 3).kind == Kind::Action);
        CHECK(MadBackupRowMap::classifyRow(2, 3).kind == Kind::Group);
    }

    TEST_CASE("an empty group list still has an action row at zero")
    {
        // A source with nothing in it: the list holds only "> Back up now".
        CHECK(MadBackupRowMap::classifyRow(0, 0).kind == Kind::Action);
    }

    TEST_CASE("a negative cursor stays a group so the caller's range check applies")
    {
        // MadVirtualList reports -1 for no cursor. The caller guards the range; what
        // matters here is that it must never come back as Action and start an op.
        CHECK(MadBackupRowMap::classifyRow(-1, 5).kind == Kind::Group);
        CHECK(MadBackupRowMap::classifyRow(-1, 0).kind == Kind::Group);
    }
}
