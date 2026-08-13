//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupItems.h  (deck-patches)
//
//  The items[] array every granular backup and restore sends to the daemon. This is
//  the exact bytes that decide WHICH files get copied over which other files, so it
//  lives alone, depends on nothing but rapidjson, and is testable without a screen.
//
//  Three shapes, and the differences are real rather than incidental:
//    backup, group list  {"group": <group key>, "rel": <path>}
//    backup, tile grid   {"bucket"|"emulator": <tile key> [, "group": <group>], "rel": <path>}
//    restore, either     {"system": <key>, "id": <path>}
//  The tile form omits "group" when there is none: BIOS files belong to no group,
//  emulator config files do. That omission is safe because the daemon reads the
//  field as "group or other", so absent and empty mean the same thing to it.
//
//  Each writer emits Key("items") plus the array, WITHOUT an enclosing object,
//  because in production it runs inside the request's already-open "params" object.
//

#ifndef ES_APP_GUIS_MAD_MAD_BACKUP_ITEMS_H
#define ES_APP_GUIS_MAD_MAD_BACKUP_ITEMS_H

#include "guis/mad/MadJson.h"

#include <string>
#include <vector>

// One ticked backup item: a file rel plus the display GROUP it belongs to (the backup
// manifest tags each item with its group so a restore can regroup it). BIOS has no
// groups and leaves it empty.
struct MadBackupItem {
    std::string group;
    std::string rel;
};

namespace MadBackupItems
{
    // One {group, rel} / {system, id} object. The restore form keys off the ticked
    // container (a group key, or the tile key) rather than the item's own group.
    inline void writeOne(MadJson::Writer& w, const bool restore, const std::string& owner,
                         const std::string& rel)
    {
        w.StartObject();
        if (restore) {
            w.Key("system");
            w.String(owner.c_str(), static_cast<rapidjson::SizeType>(owner.length()));
            w.Key("id");
        }
        else {
            w.Key("group");
            w.String(owner.c_str(), static_cast<rapidjson::SizeType>(owner.length()));
            w.Key("rel");
        }
        w.String(rel.c_str(), static_cast<rapidjson::SizeType>(rel.length()));
        w.EndObject();
    }

    // The tile pages' backup form: the tile key under its own name, then the optional
    // group, then the path.
    inline void writeTileItems(MadJson::Writer& w, const std::string& itemKeyName,
                               const std::string& key, const std::vector<MadBackupItem>& items)
    {
        w.Key("items");
        w.StartArray();
        for (const MadBackupItem& item : items) {
            w.StartObject();
            w.Key(itemKeyName.c_str(), static_cast<rapidjson::SizeType>(itemKeyName.length()));
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            if (!item.group.empty()) { // BIOS has no groups; emulator config tags each item
                w.Key("group");
                w.String(item.group.c_str(), static_cast<rapidjson::SizeType>(item.group.length()));
            }
            w.Key("rel");
            w.String(item.rel.c_str(), static_cast<rapidjson::SizeType>(item.rel.length()));
            w.EndObject();
        }
        w.EndArray();
    }

    // The tile pages' restore form: every rel under one tile key.
    inline void writeTileRestoreItems(MadJson::Writer& w, const std::string& key,
                                      const std::vector<std::string>& rels)
    {
        w.Key("items");
        w.StartArray();
        for (const std::string& rel : rels)
            writeOne(w, /*restore=*/true, key, rel);
        w.EndArray();
    }
} // namespace MadBackupItems

#endif // ES_APP_GUIS_MAD_MAD_BACKUP_ITEMS_H
