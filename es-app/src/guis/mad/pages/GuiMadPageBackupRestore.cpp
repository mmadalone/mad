//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackupRestore.cpp
//
//  See GuiMadPageBackupRestore.h.
//

#include "guis/mad/pages/GuiMadPageBackupRestore.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/pages/GuiMadPageAssetList.h"     // game-first: one game's asset tick list
#include "guis/mad/pages/GuiMadPageGranularGames.h"
#include "guis/mad/widgets/MadTileGrid.h"

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
}

GuiMadPageBackupRestore::GuiMadPageBackupRestore(GuiMadPanel* panel, const std::string& mode,
                                                 const MadTarget& target,
                                                 std::set<std::string>* selectionSink)
    : MadPage {panel, titleFor(mode)}
    , mMode {mode}
    , mCategory {"roms"}
    // restore browses the chosen backup; select/backup browse the live library.
    , mSource {mode == "restore" ? target.source : "live"}
    , mBackup {mode != "restore"} // reading the live library (select + backup), not a backup
    , mCloud {target.cloud}
    , mDest {target.dest}
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
    fetchSystems(); // the destination/source was already chosen upstream; go straight to the systems tiles
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
    // The destination was chosen upstream (GuiMadPageChooseTarget): a resolved MEGA target or a local
    // folder. mRunning is claimed inside each branch, only once the real backup actually starts.
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
    // Re-check the connection at the leaf: it may have dropped between the chooser's fail-fast check and
    // now. Firing cloud.push_game_assets while disconnected would only produce a bare failure AND leave an
    // auto-resume marker that replays the doomed op on every backend start. mRunning is claimed only once a
    // connection is confirmed.
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
        // Leaving is allowed: the daemon op keeps running (a cloud transfer is adopted by the Backup
        // Landing's "Transfers" tile; a local copy finishes on its own, rule-5 safe). Flash so the user
        // knows B did not cancel it, then pop the page (the chooser beneath is the back target).
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.",
                        4000, false);
    }
    return false; // pop the page (back to the destination/source chooser)
}

bool GuiMadPageBackupRestore::input(InputConfig* config, Input input)
{
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
