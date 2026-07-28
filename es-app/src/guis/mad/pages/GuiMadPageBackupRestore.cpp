//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackupRestore.cpp
//
//  See GuiMadPageBackupRestore.h.
//

#include "guis/mad/pages/GuiMadPageBackupRestore.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadFolderPicker.h" // backup: change folder; restore: browse for a backup folder
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageAssetList.h"     // game-first: one game's asset tick list
#include "guis/mad/pages/GuiMadPageBackends.h"       // GuiMadPageBackendChoice (the restore "change backup" list)
#include "guis/mad/pages/GuiMadPageGranularGames.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <tuple>

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

    // "YYYYmmddTHHMMSS" -> "YYYY-MM-DD HH:MM"; anything else passes through.
    std::string fmtWhen(const std::string& created)
    {
        if (created.size() == 15 && created[8] == 'T')
            return created.substr(0, 4) + "-" + created.substr(4, 2) + "-" + created.substr(6, 2) + " " +
                   created.substr(9, 2) + ":" + created.substr(11, 2);
        return created;
    }

    // Keep the bar to one line: truncate a long path from the LEFT (the tail is the meaningful part).
    std::string shortPath(const std::string& p)
    {
        if (p.size() <= 42)
            return p;
        return "..." + p.substr(p.size() - 39);
    }

    // A pickable-row id that can never collide with a real backup id (an absolute path / "cloud:<ts>").
    constexpr const char* kBrowseSentinel {"\x01""browse"};
    constexpr const char* kCloudRpc {"granular.cloud_sources"};
}

GuiMadPageBackupRestore::GuiMadPageBackupRestore(GuiMadPanel* panel, const std::string& mode,
                                                 std::set<std::string>* selectionSink)
    : MadPage {panel, titleFor(mode)}
    , mMode {mode}
    , mCategory {"roms"}
    , mSource {mode == "restore" ? "" : "live"} // restore resolves a source in build(); else the live library
    , mBackup {mode != "restore"}
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
    if (mMode == "backup") {
        ensureBar();
        // Load the remembered backup folder (shared with the full-config Local backup); the systems are the
        // live library, independent of the destination, so fetch them right away.
        std::weak_ptr<int> alive {pageAlive()};
        pageRequest("backup.get_dest", nullptr, [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (ok)
                mDest = MadJson::getString(payload, "dest");
            refreshBar();
        });
        fetchSystems();
    }
    else if (mMode == "restore") {
        ensureBar();
        resolveDefaultSource(); // latest local backup -> its systems (bar shows it; Y picks another)
    }
    else {
        fetchSystems(); // select: the live library's systems, no bar
    }
}

// ── the destination / source bar ─────────────────────────────────────────────

void GuiMadPageBackupRestore::ensureBar()
{
    if (!hasBar() || mBar != nullptr)
        return;
    // {1,1} = auto-size width AND height -> a single-line label sized to its text (no wrap into the grid).
    mBar = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                           MadTheme::color(MadColor::Title), ALIGN_LEFT, ALIGN_CENTER,
                                           glm::ivec2 {1, 1});
    mBar->setPosition(mViewportPos.x, mViewportPos.y);
    addChild(mBar.get());
    refreshBar();
}

std::string GuiMadPageBackupRestore::barText() const
{
    if (mBackup) {
        if (mCloud)
            return "Save to:  MEGA cloud       ( X: use On this Deck )";
        const std::string dest {mDest.empty() ? "(loading...)" : shortPath(mDest)};
        return "Save to:  On this Deck  " + dest + "       ( Y: change folder    X: use MEGA )";
    }
    const std::string label {mHasSource ? fmtWhen(mSrcCreated) + "  (" + std::to_string(mSrcCount) +
                                              (mSrcCount == 1 ? " game)" : " games)")
                                        : "(no backup found)"};
    if (mCloud)
        return "Restore from:  MEGA  " + label + "       ( Y: change    X: use On this Deck )";
    return "Restore from:  On this Deck  " + label + "       ( Y: change    X: use MEGA )";
}

void GuiMadPageBackupRestore::refreshBar()
{
    if (mBar != nullptr)
        mBar->setText(barText());
}

void GuiMadPageBackupRestore::toggleCloud()
{
    if (mChecking)
        return;
    if (!mCloud) {
        // -> MEGA: verify the connection first (fail-fast, and don't strand an auto-resume marker later).
        mChecking = true;
        footer()->setStatus("Checking MEGA...");
        std::weak_ptr<int> alive {pageAlive()};
        pageRequest(
            "cloud.status", nullptr,
            [this, alive](bool ok, const rapidjson::Value& payload) {
                if (alive.expired())
                    return;
                mChecking = false;
                footer()->setStatus("");
                if (!ok || !MadJson::getBool(payload, "connected")) {
                    footer()->flash("Not connected to MEGA. Run the cloud setup in Desktop Mode first.",
                                    5000, true);
                    return;
                }
                mCloud = true;
                if (!mBackup)
                    resolveDefaultSource();
                refreshBar();
            },
            30000);
        return;
    }
    mCloud = false;
    if (!mBackup)
        resolveDefaultSource();
    refreshBar();
}

void GuiMadPageBackupRestore::changeTarget()
{
    if (mBackup) {
        if (mCloud) {
            footer()->flash("MEGA has no folder to pick - X switches back to On this Deck.", 3500, false);
            return;
        }
        // change (and remember) the local backup folder; validated + persisted like the full-backup dest.
        std::weak_ptr<int> alive {pageAlive()};
        mWindow->pushGui(new GuiMadFolderPicker(
            [this, alive](const std::string& path) {
                if (alive.expired() || path.empty())
                    return;
                pageRequest(
                    "backup.set_dest",
                    [path](MadJson::Writer& w) {
                        w.Key("dest");
                        w.String(path.c_str(), static_cast<rapidjson::SizeType>(path.length()));
                    },
                    [this, alive](bool ok, const rapidjson::Value& payload) {
                        if (alive.expired())
                            return;
                        if (!ok) {
                            footer()->flash("Couldn't use that folder: " +
                                                MadJson::getString(payload, "message", "error"),
                                            5000, true);
                            return;
                        }
                        mDest = MadJson::getString(payload, "dest");
                        refreshBar();
                    });
            },
            "PICK A BACKUP DESTINATION"));
        return;
    }
    openSourcePicker(); // restore
}

// ── restore: resolve / pick a source ─────────────────────────────────────────

void GuiMadPageBackupRestore::resolveDefaultSource()
{
    mHasSource = false;
    // Tear down the OLD source's grid + clear mSource NOW (synchronously), before the possibly-slow fetch:
    // otherwise the previous kind's tiles stay interactive and A would drill into (and restore from) the
    // stale source while the bar already shows the new one. No grid = nothing drillable until it resolves.
    mSource.clear();
    mSystems.clear();
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
        mPanel->refreshHelpPrompts(); // no grid -> clear its stale "A: open" prompt during the fetch
    }
    const int gen {++mSrcGen}; // a later toggle / pick bumps mSrcGen and supersedes this resolve
    setLoadingText(mCloud ? "Looking on MEGA..." : "Looking for backups...");
    std::weak_ptr<int> alive {pageAlive()};
    const std::string category {mCategory};
    auto handle = [this, alive, gen](bool ok, const rapidjson::Value& payload, bool cloud) {
        if (alive.expired() || gen != mSrcGen)
            return;
        // pick the newest source of this kind (the backend already sorts newest-first).
        std::string id, created;
        int count {0};
        if (ok) {
            const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    if (!cloud && MadJson::getString(s, "kind") != "local")
                        continue;
                    id = MadJson::getString(s, "id");
                    created = MadJson::getString(s, "created");
                    count = MadJson::getInt(s, "count", 0);
                    break;
                }
        }
        setLoadingText("");
        if (id.empty()) {
            mHasSource = false;
            mSource.clear();
            mSystems.clear();
            if (mGrid != nullptr) {
                removeChild(mGrid.get());
                mGrid.reset();
            }
            setLoadingText(cloud ? "No backup on MEGA yet." : "No local backup found. Press Y to browse.");
            refreshBar();
            return;
        }
        applySource(id, created, count);
    };
    if (mCloud) {
        pageRequest(kCloudRpc, nullptr,
                    [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, true); }, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [category](MadJson::Writer& w) {
                w.Key("category");
                w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
            },
            [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, false); }, 10000);
    }
    refreshBar();
}

void GuiMadPageBackupRestore::applySource(const std::string& id, const std::string& created, int count)
{
    ++mSrcGen; // supersede any in-flight resolve so it can't overwrite this (user-picked) source
    mSource = id;
    mSrcCreated = created;
    mSrcCount = count;
    mHasSource = true;
    refreshBar();
    fetchSystems();
}

void GuiMadPageBackupRestore::openSourcePicker()
{
    // Fetch the CURRENT kind's backups, then present a flat self-popping list (GuiMadPageBackendChoice).
    const bool cloud {mCloud};
    footer()->setStatus(cloud ? "Looking on MEGA..." : "Looking for backups...");
    std::weak_ptr<int> alive {pageAlive()};
    const std::string category {mCategory};
    const std::string current {mSource};
    auto present = [this, alive, cloud, current](bool ok, const rapidjson::Value& payload) {
        if (alive.expired())
            return;
        footer()->setStatus("");
        // options = (id, "date - N games"); the parallel `srcs` snapshot carries created/count for apply.
        std::vector<std::pair<std::string, std::string>> options;
        std::vector<std::tuple<std::string, std::string, int>> srcs; // id, created, count
        if (ok) {
            const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    if (!cloud && MadJson::getString(s, "kind") != "local")
                        continue;
                    const std::string id {MadJson::getString(s, "id")};
                    if (id.empty())
                        continue;
                    const std::string cr {MadJson::getString(s, "created")};
                    const int cnt {MadJson::getInt(s, "count", 0)};
                    options.emplace_back(id, fmtWhen(cr) + "   -   " + std::to_string(cnt) +
                                                 (cnt == 1 ? " game" : " games"));
                    srcs.emplace_back(id, cr, cnt);
                }
        }
        if (cloud && srcs.empty()) {
            footer()->flash("No backup on MEGA yet.", 3500, false);
            return;
        }
        if (!cloud)
            options.emplace_back(kBrowseSentinel, "Browse for a folder...");
        std::weak_ptr<int> a2 {pageAlive()};
        mPanel->pushPage(new GuiMadPageBackendChoice(
            mPanel, "CHOOSE A BACKUP", cloud ? "MEGA backups:" : "On this Deck:", options, current,
            [this, a2, srcs](const std::string& id) {
                if (a2.expired())
                    return;
                if (id == kBrowseSentinel) {
                    browseForSource();
                    return;
                }
                for (const auto& s : srcs)
                    if (std::get<0>(s) == id) {
                        applySource(id, std::get<1>(s), std::get<2>(s));
                        return;
                    }
            }));
    };
    if (cloud) {
        pageRequest(kCloudRpc, nullptr, present, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [category](MadJson::Writer& w) {
                w.Key("category");
                w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
            },
            present, 10000);
    }
}

void GuiMadPageBackupRestore::browseForSource()
{
    std::weak_ptr<int> alive {pageAlive()};
    const std::string category {mCategory};
    mWindow->pushGui(new GuiMadFolderPicker(
        [this, alive, category](const std::string& path) {
            if (alive.expired() || path.empty())
                return;
            footer()->setStatus("Looking for backups...");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                "granular.sources_under",
                [path, category](MadJson::Writer& w) {
                    w.Key("path");
                    w.String(path.c_str(), static_cast<rapidjson::SizeType>(path.length()));
                    w.Key("category");
                    w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
                },
                [this, a2](bool ok, const rapidjson::Value& payload) {
                    if (a2.expired())
                        return;
                    footer()->setStatus("");
                    std::string id, created;
                    int count {0};
                    if (ok) {
                        const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
                        if (arr.IsArray() && arr.Size() > 0) {
                            const rapidjson::Value& s {arr[0]}; // newest first
                            id = MadJson::getString(s, "id");
                            created = MadJson::getString(s, "created");
                            count = MadJson::getInt(s, "count", 0);
                        }
                    }
                    if (id.empty()) {
                        footer()->flash("No backup found in that folder.", 3500, false);
                        return;
                    }
                    mCloud = false; // a browsed folder is always a local source
                    applySource(id, created, count);
                },
                60000);
        },
        "PICK A BACKUP FOLDER"));
}

// ── the per-system tiles ────────────────────────────────────────────────────

void GuiMadPageBackupRestore::fetchSystems()
{
    setLoadingText("Loading systems...");
    const std::string source {mSource};
    const std::string category {mCategory};
    const int gen {mSrcGen}; // a newer source pick supersedes this systems fetch (restore); no-op for backup
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.browse",
        [source, category](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
        },
        [this, alive, gen](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || gen != mSrcGen)
                return;
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
    // reassign mGrid and free the old one while its raw pointer is still in mChildren).
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
    }
    if (mSystems.empty()) {
        setLoadingText(mBackup ? "No game systems found." : "This backup has no games.");
        return;
    }
    const float barH {hasBar() ? Font::get(FONT_SIZE_SMALL)->getHeight() * 1.8f : 0.0f};
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
    mGrid->setPosition(mViewportPos.x, mViewportPos.y + barH);
    mGrid->setSize(mViewportSize.x, mViewportSize.y - barH);
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

// ── the running backup / restore job (lives on this root page) ──────────────

void GuiMadPageBackupRestore::openGameAssets(const std::string& system, const std::string& stem,
                                             const std::string& name, const std::string& art)
{
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
            auto start = [this, source, writeGames] {
                if (mRunning)
                    return;
                clearRunStream();
                mRunning = true;
                footer()->setStatus("Restoring...");
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
    // The destination is whatever the bar shows (MEGA or the remembered local folder). mRunning is claimed
    // inside each branch, only once the real backup actually starts.
    if (mCloud)
        beginAssetsCloud(system, stem, keys);
    else
        beginAssetsLocal(system, stem, keys, mDest);
}

void GuiMadPageBackupRestore::beginAssetsLocal(const std::string& system, const std::string& stem,
                                               const std::vector<std::string>& keys,
                                               const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true; // claim synchronously so a re-entrant X sees busy()
    footer()->setStatus("Backing up...");
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
            if (!dest.empty()) {
                w.Key("dest");
                w.String(dest.c_str(), static_cast<rapidjson::SizeType>(dest.length()));
            }
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
    // Re-check the connection at the moment of upload (it may have dropped since the bar was toggled).
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA...");
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
            footer()->setStatus("Uploading to MEGA...");
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
                if (cloud) {
                    const int failed {MadJson::getInt(data, "failed", 0)};
                    if (failed > 0)
                        footer()->flash("Backed up to MEGA, but " + std::to_string(failed) +
                                            " file(s) failed to upload. Check the log.",
                                        9000, true);
                    else
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
        // Leaving is allowed: the daemon op keeps running (a cloud transfer is adopted by the Backup
        // Landing's "Transfers" tile; a local copy finishes on its own, rule-5 safe).
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.",
                        4000, false);
    }
    return false; // pop the page (back to the landing / restore hub)
}

bool GuiMadPageBackupRestore::input(InputConfig* config, Input input)
{
    if (hasBar() && input.value != 0 && config->isMappedTo("x", input)) {
        toggleCloud();
        return true;
    }
    if (hasBar() && input.value != 0 && config->isMappedTo("y", input)) {
        changeTarget();
        return true;
    }
    return mGrid != nullptr && mGrid->input(config, input);
}

void GuiMadPageBackupRestore::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageBackupRestore::getHelpPrompts()
{
    return mGrid != nullptr ? mGrid->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageBackupRestore::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void GuiMadPageBackupRestore::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}
