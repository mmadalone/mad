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
#include "guis/mad/MadTheme.h" // routerIconPath (per-game.png emblem)
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (restore source picker)
#include "guis/mad/pages/GuiMadPageGranularGames.h"
#include "guis/mad/widgets/MadTileGrid.h"

namespace
{
    std::string titleFor(const std::string& mode)
    {
        return mode == "select" ? "CHOOSE GAMES" : "RESTORE GAMES";
    }
}

GuiMadPageBackupRestore::GuiMadPageBackupRestore(GuiMadPanel* panel, const std::string& mode,
                                                 std::set<std::string>* selectionSink)
    : MadPage {panel, titleFor(mode)}
    , mMode {mode}
    , mCategory {"roms"}
    , mSource {mode == "restore" ? "" : "live"} // select browses the live library; restore a backup
    , mBackup {mode == "select"}
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
        fetchSources();     // choose a backup, then its systems
    else
        fetchSystems();     // select mode: the live library's systems
}

// ── restore: choose a backup source ─────────────────────────────────────────

void GuiMadPageBackupRestore::fetchSources()
{
    setLoadingText("Looking for backups…");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.sources", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                setLoadingText("");
                footer()->flash("Couldn't list backups: " +
                                    MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            std::vector<std::pair<std::string, std::string>> options; // (path, label)
            const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    if (MadJson::getString(s, "kind") != "local")
                        continue; // cloud sources arrive in a later pass
                    const std::string id {MadJson::getString(s, "id")};
                    const std::string created {MadJson::getString(s, "created")};
                    std::string label {MadJson::getString(s, "label")};
                    if (!created.empty())
                        label += "  (" + created + ")";
                    options.emplace_back(id, label);
                }
            if (options.empty()) {
                setLoadingText("No local backups found yet.\nMake a per-game backup first.");
                return;
            }
            if (options.size() == 1) {
                mSource = options.front().first; // only one backup: use it directly
                fetchSystems();
                return;
            }
            if (!mPanel->isCurrentPage(this))
                return; // navigated away while the request was in flight
            setLoadingText(""); // clear "Looking for backups..." so cancelling the picker isn't a dead end
            mPanel->pushPage(new GuiMadPageBackendChoice(
                mPanel, "Choose a backup", "Pick the backup to restore games from.", options, "",
                [this, alive](const std::string& path) {
                    if (alive.expired())
                        return;
                    mSource = path;
                    mPending = Pending::ShowSystems; // deferred to update() once this chooser pops
                }));
        },
        10000);
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
    if (mSelectionSink != nullptr) { // SELECT mode (per-game backup picker): show the per-game emblem
        const std::string icon {MadTheme::routerIconPath("per-game")};
        if (!icon.empty()) {
            mEmblem = std::make_shared<ImageComponent>();
            mEmblem->setImage(icon);
            const float sz {mViewportSize.y * 0.16f};
            mEmblem->setOrigin(1.0f, 0.0f); // top-right corner of the viewport
            mEmblem->setMaxSize(sz, sz);
            mEmblem->setPosition(mViewportPos.x + mViewportSize.x, mViewportPos.y);
            addChild(mEmblem.get());
        }
    }
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
        fetchSystems();
    }
}

void GuiMadPageBackupRestore::onChildPopped()
{
    // Restore source picker cancelled (no source chosen, no fetch pending, no grid built yet): don't
    // strand the user on a blank page - tell them how to leave. A chosen source sets mPending=ShowSystems
    // (handled in update()) or mSource (1-source path), so those cases skip this.
    if (!mBackup && mGrid == nullptr && mPending == Pending::None && mSource.empty())
        setLoadingText("No backup chosen. Press B to go back.");
}

// ── the running backup / restore job (lives on this root page) ──────────────

void GuiMadPageBackupRestore::startBackup(
    const std::string& category, const std::vector<std::pair<std::string, std::string>>& items)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup",
        [category, items](MadJson::Writer& w) {
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
            w.Key("items");
            w.StartArray();
            for (const auto& it : items) {
                w.StartObject();
                w.Key("system");
                w.String(it.first.c_str(), static_cast<rapidjson::SizeType>(it.first.length()));
                w.Key("stem");
                w.String(it.second.c_str(), static_cast<rapidjson::SizeType>(it.second.length()));
                w.EndObject();
            }
            w.EndArray();
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                mRunning = false;
                footer()->flash("Couldn't start backup: " +
                                    MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            attachRunStream(MadJson::getString(payload, "stream"), /*restore=*/false);
        },
        30000);
}

void GuiMadPageBackupRestore::startRestore(
    const std::string& category, const std::string& source,
    const std::vector<std::pair<std::string, std::string>>& items)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.restore",
        [category, source, items](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
            w.Key("items");
            w.StartArray();
            for (const auto& it : items) {
                w.StartObject();
                w.Key("system");
                w.String(it.first.c_str(), static_cast<rapidjson::SizeType>(it.first.length()));
                w.Key("id");
                w.String(it.second.c_str(), static_cast<rapidjson::SizeType>(it.second.length()));
                w.EndObject();
            }
            w.EndArray();
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                mRunning = false;
                footer()->flash("Couldn't start restore: " +
                                    MadJson::getString(payload, "message", "error"),
                                5000, true);
                return;
            }
            attachRunStream(MadJson::getString(payload, "stream"), /*restore=*/true);
        },
        30000);
}

void GuiMadPageBackupRestore::attachRunStream(const std::string& token, bool restore)
{
    if (token.empty()) {
        // defensive: an OK response with no stream token would otherwise pin mRunning true forever
        mRunning = false;
        footer()->flash(std::string(restore ? "Restore" : "Backup") + " didn't start.", 5000, true);
        return;
    }
    mRunToken = token;
    std::weak_ptr<int> alive {pageAlive()};
    backend()->setStreamCallback(token, [this, alive, restore](const rapidjson::Value& data) {
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
                std::string msg {"Restored " + std::to_string(restored) + " game(s)"};
                if (replaced > 0)
                    msg += ", " + std::to_string(replaced) + " replaced";
                if (skipped > 0)
                    msg += ", " + std::to_string(skipped) + " skipped";
                footer()->flash(msg + ".", 8000, false);
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
    return false;
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
    mPending = Pending::None; // a stashed/backgrounded page must not carry a stale deferred fetch
}

void GuiMadPageBackupRestore::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}
