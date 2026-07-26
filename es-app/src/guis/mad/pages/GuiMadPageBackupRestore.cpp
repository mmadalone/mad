//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackupRestore.cpp
//
//  See GuiMadPageBackupRestore.h.
//

#include "guis/mad/pages/GuiMadPageBackupRestore.h"

#include "Window.h"
#include "guis/mad/GuiMadFolderPicker.h" // the "Browse for a folder..." local restore source browser
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h" // MadColor for the source-list rows
#include "guis/mad/pages/GuiMadPageAssetList.h"     // game-first: one game's asset tick list
#include "guis/mad/pages/GuiMadPageGranularGames.h"
#include "guis/mad/widgets/MadTileGrid.h"
#include "guis/mad/widgets/MadVirtualList.h" // the two-section (Local/Cloud) restore source list

#include <algorithm> // std::sort (merge local + browsed sources newest-first)

namespace
{
    std::string titleFor(const std::string& mode)
    {
        if (mode == "select")
            return "CHOOSE GAMES";
        if (mode == "backup")
            return "BACK UP A GAME";
        return "RESTORE GAMES";
    }

    // A pickable-row id that can never collide with a real backup id (a backup id is an ABSOLUTE path
    // -> starts with '/'); the leading control char keeps it distinct. Marks the "Browse..." action row.
    constexpr const char* kBrowseSentinel {"\x01""browse"};
}

GuiMadPageBackupRestore::GuiMadPageBackupRestore(GuiMadPanel* panel, const std::string& mode,
                                                 std::set<std::string>* selectionSink)
    : MadPage {panel, titleFor(mode)}
    , mMode {mode}
    , mCategory {"roms"}
    , mSource {mode == "restore" ? "" : "live"} // select/backup browse the live library; restore a backup
    , mBackup {mode != "restore"}               // reading the live library (select + backup), not a backup
    , mSelectionSink {selectionSink}
{
}

GuiMadPageBackupRestore::~GuiMadPageBackupRestore()
{
    clearRunStream();
}

void GuiMadPageBackupRestore::clearRunStream()
{
    if (!mRunToken.empty()) {
        backend()->clearStreamCallback(mRunToken);
        mRunToken.clear();
    }
}

void GuiMadPageBackupRestore::build()
{
    if (mMode == "restore")
        showTypeTiles();    // pick a source TYPE (Local / Cloud), then a backup, then its systems
    else
        fetchSystems();     // select mode: the live library's systems
}

// ── restore: Local / Cloud source-type tiles -> that type's backups -> its systems ──

void GuiMadPageBackupRestore::hideSystems()
{
    if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
        removeChild(mGrid.get());
        mGrid.reset();
    }
    mSystems.clear();
}

void GuiMadPageBackupRestore::hideTypeTiles()
{
    if (mTypeGrid != nullptr) {
        mTypeCookie = mTypeGrid->cursorIndex();
        removeChild(mTypeGrid.get());
        mTypeGrid.reset();
    }
}

void GuiMadPageBackupRestore::showTypeTiles()
{
    // The restore landing (and the BACK target from the backup list): two tiles, Local + Cloud, reusing
    // the Backup page's own icons. Picking a tile shows that type's backups.
    hideSourceList();
    hideSystems();
    mRView = RView::Types;
    setLoadingText("");
    mTypeGrid = std::make_shared<MadTileGrid>();
    mTypeGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mTypeGrid->setSize(mViewportSize.x, mViewportSize.y);
    std::vector<MadTileGrid::Tile> tiles;
    MadTileGrid::Tile local;
    local.key = "local";
    local.label = "Local";
    local.artPath = MadTheme::routerIconPath("backup-local");
    tiles.push_back(local);
    MadTileGrid::Tile cloud;
    cloud.key = "cloud";
    cloud.label = "Cloud (MEGA)";
    cloud.artPath = MadTheme::routerIconPath("backup-cloud-mega");
    tiles.push_back(cloud);
    mTypeGrid->setTiles(tiles);
    mTypeGrid->setCursorIndex(mTypeCookie);
    mTypeGrid->setOnPick([this](const std::string& k) { onPickType(k); });
    mTypeGrid->onFocusGained();
    addChild(mTypeGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBackupRestore::onPickType(const std::string& kind)
{
    mSourceKind = kind; // "local" | "cloud"
    showBackupList();
}

void GuiMadPageBackupRestore::showBackupList()
{
    // The chosen type's backups (date + N games). BACK target = the type tiles. The slow cloud list is
    // fetched only here (i.e. only if the user actually picks Cloud), never eagerly.
    hideTypeTiles();
    hideSystems();
    mRView = RView::List;
    setLoadingText("");
    ensureSourceList();
    rebuildSourceList();
    if (mSourceKind == "cloud") {
        if (!mCloudLoaded && !mCloudLoading)
            fetchCloudSources();
    }
    else if (!mLocalLoaded && !mLocalLoading) {
        fetchLocalSources();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBackupRestore::ensureSourceList()
{
    if (mSourceList != nullptr)
        return;
    mSourceList = std::make_shared<MadVirtualList>();
    mSourceList->setPosition(mViewportPos.x, mViewportPos.y);
    mSourceList->setSize(mViewportSize.x, mViewportSize.y);
    mSourceList->setOnSelect([this](int i) { onPickSource(i); });
    addChild(mSourceList.get());
    mSourceList->onFocusGained();
}

void GuiMadPageBackupRestore::hideSourceList()
{
    if (mSourceList != nullptr) {
        removeChild(mSourceList.get());
        mSourceList.reset();
    }
}

void GuiMadPageBackupRestore::rebuildSourceList()
{
    if (mSourceList == nullptr)
        return;
    const bool cloud {mSourceKind == "cloud"};
    // LOCAL shows the remembered+default backups PLUS any found via the folder browser, de-duped by id
    // (a backup's absolute path) and newest-first. Kept in a local vector so a slow granular.sources
    // callback that clears mLocalSrc can never wipe the browsed entries.
    std::vector<Src> localCombined;
    if (!cloud) {
        localCombined = mLocalSrc;
        for (const Src& b : mBrowsedSrc) {
            bool dup {false};
            for (const Src& e : localCombined)
                if (e.id == b.id) { dup = true; break; }
            if (!dup)
                localCombined.push_back(b);
        }
        std::sort(localCombined.begin(), localCombined.end(),
                  [](const Src& a, const Src& c) { return a.created > c.created; });
    }
    // "loaded" gates the "loading..." note; browsed results count as loaded even if the default scan is
    // still in flight, so a browse result never hides behind "loading...".
    const bool loaded {cloud ? mCloudLoaded : (mLocalLoaded || !mBrowsedSrc.empty())};
    const std::vector<Src>& src {cloud ? mCloudSrc : localCombined};
    std::vector<MadVirtualList::Row> rows;
    mSourceRowId.clear();
    const unsigned int note {MadTheme::color(MadColor::Secondary)};
    const unsigned int item {MadTheme::color(MadColor::Primary)};
    const unsigned int action {MadTheme::color(MadColor::Title)};
    auto pushNote = [&](const std::string& t) {
        rows.push_back({t, note});
        mSourceRowId.push_back("");
    };
    // The Browse action is LOCAL-only and always available (independent of the backup scan).
    if (!cloud) {
        rows.push_back({"Browse for a folder...", action});
        mSourceRowId.push_back(kBrowseSentinel);
    }
    if (!loaded)
        pushNote(cloud ? "Looking on MEGA..." : "loading...");
    else if (cloud && !mCloudConnected)
        pushNote("(not connected - run the cloud setup in Desktop Mode)");
    else if (src.empty())
        pushNote(cloud ? "(none yet - back some games up to MEGA first)"
                       : "(none yet - make a per-game backup first, or Browse above)");
    else
        for (const Src& s : src) {
            rows.push_back({fmtSourceLabel(s.created, s.count), item});
            mSourceRowId.push_back(s.id);
        }

    const int prev {mSourceList->cursor()};
    mSourceList->setRows(rows, /*keepCursor=*/true);
    // Land the cursor on the first BACKUP row when the kept position isn't a real backup - including when
    // it sits on the always-present Browse action (row 0), so a fresh list with backups highlights the
    // newest one, not "Browse...". If there are NO backups, stay on Browse (don't force-move onto a note).
    const bool onBrowse {prev >= 0 && prev < static_cast<int>(mSourceRowId.size()) &&
                         mSourceRowId[prev] == kBrowseSentinel};
    if (prev < 0 || prev >= static_cast<int>(mSourceRowId.size()) || mSourceRowId[prev].empty() ||
        onBrowse) {
        int firstBackup {-1}, firstPickable {-1};
        for (int i = 0; i < static_cast<int>(mSourceRowId.size()); ++i) {
            if (mSourceRowId[i].empty())
                continue;
            if (firstPickable < 0)
                firstPickable = i;
            if (mSourceRowId[i] != kBrowseSentinel) { firstBackup = i; break; }
        }
        const int land {firstBackup >= 0 ? firstBackup : (onBrowse ? -1 : firstPickable)};
        if (land >= 0)
            mSourceList->setCursor(land);
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBackupRestore::onPickSource(int index)
{
    if (index < 0 || index >= static_cast<int>(mSourceRowId.size()))
        return;
    const std::string id {mSourceRowId[index]};
    if (id.empty()) {
        footer()->flash("No backup to choose here yet.", 2500, false);
        return;
    }
    if (id == kBrowseSentinel) {
        openSourceBrowser();
        return;
    }
    mSource = id;
    mPending = Pending::ShowSystems; // deferred to update(): swap the list out + fetch the systems tiles
}

void GuiMadPageBackupRestore::fetchLocalSources()
{
    mLocalLoading = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.sources", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mLocalLoading = false;
            mLocalLoaded = true;
            mLocalSrc.clear();
            if (ok) {
                const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
                if (arr.IsArray())
                    for (const rapidjson::Value& s : arr.GetArray()) {
                        if (MadJson::getString(s, "kind") != "local")
                            continue;
                        mLocalSrc.push_back({MadJson::getString(s, "id"),
                                             MadJson::getString(s, "created"),
                                             MadJson::getInt(s, "count", 0)});
                    }
            }
            if (mRView == RView::List && mSourceKind != "cloud")
                rebuildSourceList();
        },
        10000);
}

void GuiMadPageBackupRestore::fetchCloudSources()
{
    mCloudLoading = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.cloud_sources", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mCloudLoading = false;
            mCloudLoaded = true;
            mCloudConnected = ok && MadJson::getBool(payload, "connected");
            mCloudSrc.clear();
            if (ok) {
                const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
                if (arr.IsArray())
                    for (const rapidjson::Value& s : arr.GetArray())
                        mCloudSrc.push_back({MadJson::getString(s, "id"),
                                             MadJson::getString(s, "created"),
                                             MadJson::getInt(s, "count", 0)});
            }
            if (mRView == RView::List && mSourceKind == "cloud")
                rebuildSourceList();
        },
        // list-games cats each set's manifest over the network, so give it plenty of room.
        200000);
}

void GuiMadPageBackupRestore::openSourceBrowser()
{
    // A Window-level modal (like openDestPicker), NOT a panel page: it captures input, so the panel
    // underneath can't be navigated while it is up. On a chosen folder, scan it for backups.
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadFolderPicker(
        [this, alive](const std::string& path) {
            if (alive.expired() || path.empty()) // empty == cancelled
                return;
            fetchLocalSourcesUnder(path);
        },
        "PICK A BACKUP FOLDER"));
}

void GuiMadPageBackupRestore::fetchLocalSourcesUnder(const std::string& path)
{
    footer()->flash("Looking for backups in that folder...", 2000, false);
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.sources_under",
        [path](MadJson::Writer& w) {
            w.Key("path");
            w.String(path.c_str(), static_cast<rapidjson::SizeType>(path.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                footer()->flash("Couldn't read that folder.", 3000, true);
                return;
            }
            int found {0}, added {0};
            const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    const std::string id {MadJson::getString(s, "id")};
                    if (id.empty())
                        continue;
                    ++found;
                    bool dup {false};
                    for (const Src& e : mBrowsedSrc)
                        if (e.id == id) { dup = true; break; }
                    if (!dup)
                        for (const Src& e : mLocalSrc)
                            if (e.id == id) { dup = true; break; }
                    if (dup)
                        continue;
                    mBrowsedSrc.push_back({id, MadJson::getString(s, "created"),
                                           MadJson::getInt(s, "count", 0)});
                    ++added;
                }
            if (found == 0)
                footer()->flash("No backups found in that folder.", 3000, false);
            else if (added == 0)
                footer()->flash("Those backups are already listed.", 2500, false);
            else
                footer()->flash(std::to_string(added) + " backup(s) added from that folder.", 2500,
                                false);
            if (mRView == RView::List && mSourceKind != "cloud")
                rebuildSourceList();
        },
        // an arbitrary folder scan is slow=True on the backend (may hold many entries).
        60000);
}

std::string GuiMadPageBackupRestore::fmtSourceLabel(const std::string& created, int count)
{
    std::string when {created};
    if (created.size() == 15 && created[8] == 'T') // "YYYYmmddTHHMMSS" -> "YYYY-MM-DD HH:MM:SS"
        when = created.substr(0, 4) + "-" + created.substr(4, 2) + "-" + created.substr(6, 2) + " " +
               created.substr(9, 2) + ":" + created.substr(11, 2) + ":" + created.substr(13, 2);
    return when + "   -   " + std::to_string(count) + (count == 1 ? " game" : " games");
}

// ── the per-system tiles ────────────────────────────────────────────────────

void GuiMadPageBackupRestore::fetchSystems()
{
    setLoadingText("Loading systems…");
    const std::string source {mSource};
    const std::string category {mCategory};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.browse",
        [source, category](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (mRView != RView::Systems)
                return; // backed out to the list/tiles while this browse was in flight - drop it
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load systems: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mSystems.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "systems")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray())
                    mSystems.push_back({MadJson::getString(s, "key"), MadJson::getString(s, "label"),
                                        MadJson::getString(s, "art"), MadJson::getInt(s, "count", 0)});
            rebuildSystems();
        },
        10000);
}

void GuiMadPageBackupRestore::rebuildSystems()
{
    // Defensive: never leave a prior grid as a dangling child (a second granular.browse would otherwise
    // reassign mGrid and free the old one while its raw pointer is still in mChildren). NB: grid-only -
    // do NOT hideSystems() here, that clears mSystems which the empty-check below relies on.
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
    }
    if (mSystems.empty()) {
        setLoadingText(mBackup ? "No game systems found." : "This backup has no games.");
        return;
    }
    std::vector<MadTileGrid::Tile> tiles;
    for (const Sys& sys : mSystems) {
        MadTileGrid::Tile tile;
        tile.key = sys.key;
        tile.label = sys.label.empty() ? sys.key : sys.label;
        tile.sublabel = std::to_string(sys.count) + (sys.count == 1 ? " game" : " games");
        tile.artPath = sys.art;
        tiles.push_back(tile);
    }
    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mGrid->setSize(mViewportSize.x, mViewportSize.y);
    mGrid->setTiles(tiles);
    mGrid->setOnPick([this](const std::string& key) { onPickSystem(key); });
    mGrid->setCursorIndex(mGridCookie);
    mGrid->onFocusGained();
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBackupRestore::onPickSystem(const std::string& key)
{
    std::string label {key};
    for (const Sys& sys : mSystems)
        if (sys.key == key) {
            label = sys.label.empty() ? sys.key : sys.label;
            break;
        }
    mPanel->pushPage(new GuiMadPageGranularGames(mPanel, this, mCategory, mSource, mMode, key, label,
                                                 mSelectionSink));
}

void GuiMadPageBackupRestore::update(int deltaTime)
{
    MadPage::update(deltaTime);
    if (mPending == Pending::ShowSystems) {
        mPending = Pending::None;
        hideSourceList(); // restore: leave the backup list before showing the per-system tiles
        hideTypeTiles();
        mRView = RView::Systems;
        fetchSystems();
    }
}

// ── the running backup / restore job (lives on this root page) ──────────────

void GuiMadPageBackupRestore::openGameAssets(const std::string& system, const std::string& stem,
                                             const std::string& name, const std::string& art)
{
    // game-first: drill from the per-system game list into ONE game's asset tick list. In restore mode the
    // leaf RESTORES the ticked groups (source = the chosen backup); in backup mode it BACKS them up (source
    // = "live"). Either way the op lives on THIS durable root, so popping the leaf never orphans it.
    const bool restore {mMode == "restore"};
    mPanel->pushPage(new GuiMadPageAssetList(mPanel, this, mSource, system, stem, name, art, restore));
}

void GuiMadPageBackupRestore::restoreAssets(const std::vector<AssetRestoreSel>& games)
{
    if (mRunning || mRestorePreviewing || games.empty())
        return;
    const std::string source {mSource};
    // one games writer shared by the preview AND the restore, so both request the identical selection.
    auto writeGames = [games](MadJson::Writer& w) {
        w.Key("games");
        w.StartArray();
        for (const AssetRestoreSel& g : games) {
            w.StartObject();
            w.Key("system");
            w.String(g.system.c_str(), static_cast<rapidjson::SizeType>(g.system.length()));
            w.Key("stem");
            w.String(g.stem.c_str(), static_cast<rapidjson::SizeType>(g.stem.length()));
            w.Key("keys");
            w.StartArray();
            for (const std::string& k : g.keys)
                w.String(k.c_str(), static_cast<rapidjson::SizeType>(k.length()));
            w.EndArray();
            w.EndObject();
        }
        w.EndArray();
    };
    mRestorePreviewing = true; // in flight until the preview responds (guards a double X stacking dialogs)
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.restore_assets_preview",
        [source, writeGames](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            writeGames(w);
        },
        [this, alive, source, writeGames](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mRestorePreviewing = false;
            if (!ok) {
                footer()->flash("Couldn't check the backup: " +
                                    MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            int replace {0};
            const rapidjson::Value& arr {MadJson::getMember(payload, "replace")};
            if (arr.IsArray())
                replace = static_cast<int>(arr.Size());
            // fire the actual restore (claims mRunning synchronously, then streams granular.restore_assets)
            auto start = [this, source, writeGames] {
                if (mRunning)
                    return;
                clearRunStream();
                mRunning = true;
                footer()->setStatus("Restoring…");
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore_assets",
                    [source, writeGames](MadJson::Writer& w) {
                        w.Key("source");
                        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
                        writeGames(w);
                    },
                    [this, a2](bool ok2, const rapidjson::Value& payload2) {
                        if (a2.expired())
                            return;
                        if (!ok2) {
                            mRunning = false;
                            footer()->setStatus("");
                            footer()->flash("Couldn't start restore: " +
                                                MadJson::getString(payload2, "message", "error"),
                                            5000, true);
                            return;
                        }
                        // assets=true: this restore counts asset FILES, not games (see the terminal).
                        attachRunStream(MadJson::getString(payload2, "stream"), /*restore=*/true,
                                        /*assets=*/true);
                    },
                    30000);
            };
            if (replace > 0) {
                std::weak_ptr<int> a3 {pageAlive()};
                mWindow->pushGui(new MadMsgBox(
                    std::to_string(replace) + " item(s) already on disk will be REPLACED. A recoverable "
                    "copy is saved aside first. Continue?",
                    "YES", [a3, start] { if (!a3.expired()) start(); }, "CANCEL", nullptr));
            }
            else {
                start();
            }
        },
        20000);
}

void GuiMadPageBackupRestore::startGameAssets(const std::string& system, const std::string& stem,
                                              const std::vector<std::string>& keys)
{
    if (mRunning)
        return;
    // Ask WHERE the backup goes before running anything. B on the dialog just closes it (a safe cancel -
    // GuiMsgBox's default mBackFunc is null), and because the dialog captures input the leaf underneath
    // can't fire a second X while it is up. mRunning is claimed inside each branch, only once the real
    // backup actually starts, so a cancelled chooser/picker pins nothing.
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "Where should this game's backup go?",
        "ON THIS DECK",
        [this, alive, system, stem, keys] {
            if (alive.expired())
                return;
            // pick a destination FOLDER, then back up locally into it.
            mWindow->pushGui(new GuiMadFolderPicker(
                [this, alive, system, stem, keys](const std::string& path) {
                    if (alive.expired() || path.empty()) // empty == cancelled
                        return;
                    beginAssetsLocal(system, stem, keys, path);
                },
                "PICK A BACKUP DESTINATION"));
        },
        "MEGA CLOUD",
        [this, alive, system, stem, keys] {
            if (alive.expired())
                return;
            beginAssetsCloud(system, stem, keys);
        }));
}

void GuiMadPageBackupRestore::beginAssetsLocal(const std::string& system, const std::string& stem,
                                               const std::vector<std::string>& keys,
                                               const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true; // claim synchronously so a re-entrant X sees busy()
    footer()->setStatus("Backing up…");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_assets",
        [system, stem, keys, dest](MadJson::Writer& w) {
            w.Key("items");
            w.StartArray();
            w.StartObject();
            w.Key("system");
            w.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
            w.Key("stem");
            w.String(stem.c_str(), static_cast<rapidjson::SizeType>(stem.length()));
            w.Key("keys");
            w.StartArray();
            for (const std::string& k : keys)
                w.String(k.c_str(), static_cast<rapidjson::SizeType>(k.length()));
            w.EndArray();
            w.EndObject();
            w.EndArray();
            w.Key("dest");
            w.String(dest.c_str(), static_cast<rapidjson::SizeType>(dest.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                mRunning = false;
                footer()->setStatus("");
                footer()->flash("Couldn't start backup: " +
                                    MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            attachRunStream(MadJson::getString(payload, "stream"), /*restore=*/false, /*assets=*/true,
                            /*cloud=*/false);
        },
        30000);
}

void GuiMadPageBackupRestore::beginAssetsCloud(const std::string& system, const std::string& stem,
                                               const std::vector<std::string>& keys)
{
    if (mRunning)
        return;
    // Guard the upload behind a connection check, exactly like the whole-ROM cloud path
    // (GuiMadPageBackup::cloudGuard): if MEGA isn't set up, firing cloud.push_game_assets would only
    // produce a bare failure AND leave an auto-resume marker that replays the doomed op on every backend
    // start. cloud.status is a fast bounded check; mRunning is claimed only once a connection is confirmed.
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA…");
    pageRequest(
        "cloud.status", nullptr,
        [this, alive, system, stem, keys](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            footer()->setStatus("");
            if (!ok || !MadJson::getBool(payload, "connected")) {
                footer()->flash("Not connected to MEGA. Run the cloud setup in Desktop Mode first.",
                                6000, true);
                return;
            }
            if (mRunning) // a concurrent op claimed the root while the status check was in flight
                return;
            clearRunStream();
            mRunning = true;
            footer()->setStatus("Uploading to MEGA…");
            pageRequest(
                "cloud.push_game_assets",
                [system, stem, keys](MadJson::Writer& w) {
                    w.Key("items");
                    w.StartArray();
                    w.StartObject();
                    w.Key("system");
                    w.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
                    w.Key("stem");
                    w.String(stem.c_str(), static_cast<rapidjson::SizeType>(stem.length()));
                    w.Key("keys");
                    w.StartArray();
                    for (const std::string& k : keys)
                        w.String(k.c_str(), static_cast<rapidjson::SizeType>(k.length()));
                    w.EndArray();
                    w.EndObject();
                    w.EndArray();
                },
                [this, alive](bool ok2, const rapidjson::Value& payload2) {
                    if (alive.expired())
                        return;
                    if (!ok2) {
                        mRunning = false;
                        footer()->setStatus("");
                        footer()->flash("Couldn't start upload: " +
                                            MadJson::getString(payload2, "message", "error"),
                                        6000, true);
                        return;
                    }
                    attachRunStream(MadJson::getString(payload2, "stream"), /*restore=*/false,
                                    /*assets=*/true, /*cloud=*/true);
                },
                30000);
        },
        30000);
}

void GuiMadPageBackupRestore::attachRunStream(const std::string& token, bool restore, bool assets,
                                              bool cloud)
{
    if (token.empty()) {
        // defensive: an OK response with no stream token would otherwise pin mRunning true forever
        mRunning = false;
        footer()->setStatus("");
        footer()->flash(std::string(restore ? "Restore" : "Backup") + " didn't start.", 5000, true);
        return;
    }
    mRunToken = token;
    std::weak_ptr<int> alive {pageAlive()};
    backend()->setStreamCallback(token, [this, alive, restore, assets, cloud](const rapidjson::Value& data) {
        if (alive.expired())
            return;
        if (MadJson::getBool(data, "closed")) {
            if (mRunning) {
                mRunning = false;
                footer()->setStatus("");
                footer()->flash("The operation ended unexpectedly.", 6000, true);
            }
            return;
        }
        if (MadJson::getBool(data, "done")) {
            if (!mRunning)
                return; // idempotent on a duplicate terminal event
            mRunning = false;
            footer()->setStatus("");
            const int rc {MadJson::getInt(data, "rc", -1)};
            if (rc != 0) {
                // the engine threw / was cancelled: no summary was produced, so never claim a 0-count
                // "success" - surface the failure (and its message) with error styling.
                if (MadJson::getBool(data, "stopped")) {
                    footer()->flash("Cancelled.", 6000, true);
                }
                else {
                    const std::string err {MadJson::getString(data, "error")};
                    footer()->flash(std::string(restore ? "Restore failed" : "Backup failed") +
                                        (err.empty() ? "." : ": " + err),
                                    8000, true);
                }
                return;
            }
            if (restore) {
                const int restored {MadJson::getInt(data, "restored", 0)};
                const int replaced {MadJson::getInt(data, "replaced", 0)};
                const int skipped {MadJson::getInt(data, "skipped", 0)};
                int orphaned {0};
                const rapidjson::Value& orphans {MadJson::getMember(data, "orphaned")};
                if (orphans.IsArray())
                    orphaned = static_cast<int>(orphans.Size());
                // a game-first restore (assets) counts asset FILES; the whole-ROM bulk restore counts games.
                const std::string noun {assets ? " item(s)" : " game(s)"};
                std::string msg {"Restored " + std::to_string(restored) + noun};
                if (replaced > 0)
                    msg += ", " + std::to_string(replaced) + " replaced";
                if (skipped > 0)
                    msg += ", " + std::to_string(skipped) + " skipped";
                msg += ".";
                if (restored > 0 && MadJson::getString(data, "restart_scope") == "esde")
                    msg += " Restart ES-DE to see restored media.";
                footer()->flash(msg, 9000, false);
                if (orphaned > 0) {
                    const std::string snap {MadJson::getString(data, "snapshot")};
                    mWindow->pushGui(new MadMsgBox(
                        std::to_string(orphaned) +
                            " game(s) could not be fully restored and their previous copy was moved "
                            "aside for safety. Roll them back from RECOVERY.txt in:\n\n" +
                            snap,
                        "OK", [] {}));
                }
            }
            else if (assets) {
                // game-first backup terminal. Cloud (push_game_assets) streams no per-file count, so it
                // reports a plain success; local counts the FILES copied across the game's ticked assets.
                if (cloud) {
                    footer()->flash("Backed up to MEGA.", 8000, false);
                }
                else {
                    const int copied {MadJson::getInt(data, "copied", 0)};
                    footer()->flash("Backed up " + std::to_string(copied) +
                                        (copied == 1 ? " file." : " files."),
                                    8000, false);
                }
            }
            else {
                const int copied {MadJson::getInt(data, "copied", 0)};
                const int skipped {MadJson::getInt(data, "skipped", 0)};
                std::string msg {"Backed up " + std::to_string(copied) + " game(s)"};
                if (skipped > 0)
                    msg += ", " + std::to_string(skipped) + " skipped";
                footer()->flash(msg + ".", 8000, false);
            }
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty())
            footer()->setStatus(line);
    });
}

// ── input / focus ───────────────────────────────────────────────────────────

bool GuiMadPageBackupRestore::onBackPressed()
{
    if (mRunning) {
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") +
                            " - please wait for it to finish.",
                        3000, false);
        return true; // block leaving while the job runs (this page owns it)
    }
    // restore drills Types -> List -> Systems; B walks back up one level instead of popping the page.
    if (mMode == "restore") {
        if (mRView == RView::Systems) {
            showBackupList();  // systems -> the chosen type's backup list
            return true;
        }
        if (mRView == RView::List) {
            showTypeTiles();   // backup list -> the Local/Cloud tiles
            return true;
        }
    }
    return false; // Types (or select mode) -> pop the page
}

bool GuiMadPageBackupRestore::input(InputConfig* config, Input input)
{
    if (mRView == RView::Types)
        return mTypeGrid != nullptr && mTypeGrid->input(config, input);
    if (mRView == RView::List)
        return mSourceList != nullptr && mSourceList->input(config, input);
    return mGrid != nullptr && mGrid->input(config, input);
}

void GuiMadPageBackupRestore::pageScroll(int direction)
{
    if (mRView == RView::Types) {
        if (mTypeGrid != nullptr)
            mTypeGrid->pageScroll(direction);
    }
    else if (mRView == RView::List) {
        if (mSourceList != nullptr)
            mSourceList->pageScroll(direction);
    }
    else if (mGrid != nullptr) {
        mGrid->pageScroll(direction);
    }
}

std::vector<HelpPrompt> GuiMadPageBackupRestore::getHelpPrompts()
{
    if (mRView == RView::Types)
        return mTypeGrid != nullptr ? mTypeGrid->getHelpPrompts() : std::vector<HelpPrompt>();
    if (mRView == RView::List)
        return mSourceList != nullptr ? mSourceList->getHelpPrompts() : std::vector<HelpPrompt>();
    return mGrid != nullptr ? mGrid->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageBackupRestore::onSaveFocus()
{
    if (mRView == RView::Types) {
        if (mTypeGrid != nullptr)
            mTypeCookie = mTypeGrid->cursorIndex();
    }
    else if (mRView == RView::List) {
        if (mSourceList != nullptr)
            mSourceCookie = mSourceList->cursor();
    }
    else if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
    }
    mPending = Pending::None; // a stashed/backgrounded page must not carry a stale deferred fetch
}

void GuiMadPageBackupRestore::onRestoreFocus()
{
    if (mRView == RView::Types) {
        if (mTypeGrid != nullptr) {
            mTypeGrid->setCursorIndex(mTypeCookie);
            mTypeGrid->onFocusGained();
        }
    }
    else if (mRView == RView::List) {
        if (mSourceList != nullptr) {
            mSourceList->setCursor(mSourceCookie);
            mSourceList->onFocusGained();
        }
    }
    else if (mGrid != nullptr) {
        mGrid->setCursorIndex(mGridCookie);
    }
}
