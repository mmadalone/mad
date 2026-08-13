//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupRowMap.h  (deck-patches)
//
//  What a press on the backup / restore list means. The list is laid out as: the
//  groups, then the single action row, then any extra rows a page appended below it.
//
//  This lives alone, with no dependencies, for two reasons. It is the rule the
//  phase 4b consolidation was warned about by name - Controller config puts two
//  local revert ops UNDER the action row, so "anything past the groups is the
//  action" would make "Revert emulator inputs" run a full restore - and it was
//  previously written out twice, once for what the list DOES and once for what the
//  explanation pane SAYS, which is two places to drift. One copy, no dependencies,
//  and a test that can reach it without a screen.
//

#ifndef ES_APP_GUIS_MAD_MAD_BACKUP_ROW_MAP_H
#define ES_APP_GUIS_MAD_MAD_BACKUP_ROW_MAP_H

namespace MadBackupRowMap
{
    enum class RowKind { Group, Action, Extra };

    struct RowHit {
        RowKind kind;
        int index; // the group index, or the extra-row index; 0 for the action row
    };

    // A negative index (MadVirtualList reports -1 for no cursor) classifies as a
    // Group, never as the action row, so an empty list cannot start an operation.
    // The caller still range-checks before indexing.
    inline RowHit classifyRow(const int listIndex, const int groupCount)
    {
        if (listIndex == groupCount)
            return {RowKind::Action, 0};
        if (listIndex > groupCount)
            return {RowKind::Extra, listIndex - groupCount - 1};
        return {RowKind::Group, listIndex};
    }
} // namespace MadBackupRowMap

#endif // ES_APP_GUIS_MAD_MAD_BACKUP_ROW_MAP_H
