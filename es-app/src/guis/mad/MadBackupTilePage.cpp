//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupTilePage.cpp  (deck-patches)
//

#include "guis/mad/MadBackupTilePage.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/widgets/MadTileGrid.h"

namespace
{
    constexpr const char* kAllSentinel {"\x01""all"}; // the leading "All" grid tile
} // namespace

MadBackupTilePage::MadBackupTilePage(GuiMadPanel* panel, const std::string& title,
                                     const std::string& mode, const Params& params,
                                     const TileParams& tileParams)
    : MadBackupFlowPage {panel, title, mode, params}
    , mTileParams {tileParams}
{
}

void MadBackupTilePage::build()
{
    ensureBar();
    if (mBackup) {
        std::weak_ptr<int> alive {pageAlive()};
        pageRequest("backup.get_dest", nullptr, [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (ok)
                mDest = MadJson::getString(payload, "dest");
            refreshBar();
        });
        fetchTiles();
        resolveDefaultDestination();
    }
    else {
        resolveDefaultDestination(); // probe first, then resolve the latest cloud/local backup once
    }
}

// ── the tile grid ────────────────────────────────────────────────────────────

void MadBackupTilePage::refetchForSource()
{
    fetchTiles();
}

void MadBackupTilePage::teardownContent()
{
    // The old source's tiles must not stay drillable while the new source resolves, so the grid goes
    // away entirely rather than being emptied - and the prompts follow it.
    mItems.clear();
    if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
        removeChild(mGrid.get());
        mGrid.reset();
        mPanel->refreshHelpPrompts();
    }
}

void MadBackupTilePage::fetchTiles()
{
    setLoadingText("Loading " + mParams.noun + "...");
    const std::string source {mSource};
    const int gen {mSrcGen}; // a newer source pick supersedes this fetch (restore); no-op for backup
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        mParams.contentRpc,
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive, gen](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || gen != mSrcGen)
                return;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load " + mParams.noun + ": " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mItems.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "systems")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    Item i;
                    i.key = MadJson::getString(s, "key");
                    i.label = MadJson::getString(s, "label");
                    i.art = MadJson::getString(s, "art");
                    if (!i.key.empty())
                        mItems.push_back(i);
                }
            rebuildTiles();
        },
        20000);
}

std::string MadBackupTilePage::tileArt(const Item& i) const
{
    return !i.art.empty() ? i.art : MadTheme::routerIconPath(mTileParams.categoryIcon);
}

void MadBackupTilePage::rebuildTiles()
{
    if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
        removeChild(mGrid.get());
        mGrid.reset();
    }
    if (mItems.empty()) {
        setLoadingText(mBackup ? mTileParams.emptyBackup : mTileParams.emptyRestore);
        return;
    }
    const float barH {Font::get(FONT_SIZE_SMALL)->getHeight() * 1.8f};
    std::vector<MadTileGrid::Tile> tiles;
    {   // a leading "All" tile: back up / restore EVERY tile at once (guarded by a size-aware confirm).
        MadTileGrid::Tile all;
        all.key = kAllSentinel;
        all.label = "All";
        all.artPath = MadTheme::routerIconPath(mTileParams.categoryIcon);
        tiles.push_back(all);
    }
    for (const Item& i : mItems) {
        MadTileGrid::Tile t;
        t.key = i.key;
        t.label = i.label;
        t.artPath = tileArt(i);
        tiles.emplace_back(t);
    }
    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y + barH);
    mGrid->setSize(mViewportSize.x, mViewportSize.y - barH);
    mGrid->setTiles(tiles);
    mGrid->setCursorIndex(mGridCookie);
    mGrid->setOnPick([this](const std::string& key) { onPickTile(key); });
    mGrid->onFocusGained();
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void MadBackupTilePage::onPickTile(const std::string& key)
{
    // Also refuses while an "All" size probe or restore preview is in flight: those end in a
    // confirmation that starts an op, so drilling in meanwhile could start a second one.
    if (mRunning || mPreflight || mRestorePreviewing) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    if (key == kAllSentinel) { // the leading "All" tile: every tile at once
        if (mBackup)
            backupAll();
        else
            restoreAll();
        return;
    }
    std::string label {key};
    for (const Item& i : mItems)
        if (i.key == key) {
            label = i.label;
            break;
        }
    // the leaf backs up / restores through THIS durable root, so the op survives a popped leaf.
    mPanel->pushPage(makeLeafPage(key, label));
}

// ── one tile ─────────────────────────────────────────────────────────────────

void MadBackupTilePage::writeItems(MadJson::Writer& w, const std::string& key,
                                   const std::vector<MadBackupItem>& items) const
{
    w.Key("items");
    w.StartArray();
    for (const MadBackupItem& item : items) {
        w.StartObject();
        w.Key(mTileParams.itemKeyName.c_str(),
              static_cast<rapidjson::SizeType>(mTileParams.itemKeyName.length()));
        w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
        if (!item.group.empty()) { // BIOS has no groups; emulator config tags each item with one
            w.Key("group");
            w.String(item.group.c_str(), static_cast<rapidjson::SizeType>(item.group.length()));
        }
        w.Key("rel");
        w.String(item.rel.c_str(), static_cast<rapidjson::SizeType>(item.rel.length()));
        w.EndObject();
    }
    w.EndArray();
}

void MadBackupTilePage::startBackup(const std::string& key, const std::vector<MadBackupItem>& items)
{
    if (mRunning)
        return;
    // The destination is whatever the bar shows (MEGA or the remembered local folder).
    if (mCloud)
        beginBackupCloud(key, items);
    else
        beginBackupLocal(key, items, mDest);
}

void MadBackupTilePage::beginBackupLocal(const std::string& key,
                                         const std::vector<MadBackupItem>& items,
                                         const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true; // claim synchronously so a re-entrant X sees busy()
    footer()->setStatus("Backing up " + mParams.noun + "...");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        mParams.backupRpc,
        [this, key, items, dest](MadJson::Writer& w) {
            writeItems(w, key, items);
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
            attachRunStream(MadJson::getString(payload, "stream"), /*restore=*/false, /*cloud=*/false);
        },
        30000);
}

void MadBackupTilePage::beginBackupCloud(const std::string& key,
                                         const std::vector<MadBackupItem>& items)
{
    if (mRunning)
        return;
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA...");
    pageRequest(
        "cloud.status", nullptr,
        [this, alive, key, items](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            footer()->setStatus("");
            if (!ok || !MadJson::getBool(payload, "connected")) {
                footer()->flash("Not connected to MEGA. Run the cloud setup in Desktop Mode first.",
                                6000, true);
                return;
            }
            if (mRunning)
                return;
            clearRunStream();
            mRunning = true;
            footer()->setStatus("Uploading " + mParams.noun + " to MEGA...");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                mParams.cloudPushRpc,
                [this, key, items](MadJson::Writer& w) { writeItems(w, key, items); },
                [this, a2](bool ok2, const rapidjson::Value& payload2) {
                    if (a2.expired())
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
                                    /*cloud=*/true);
                },
                30000);
        },
        30000);
}

void MadBackupTilePage::startRestore(const std::string& key, const std::vector<std::string>& rels)
{
    if (mRunning || mRestorePreviewing)
        return;
    const std::string source {mSource};
    const bool cloud {source.rfind("cloud:", 0) == 0};
    // Supersede if the bar's source changes (X/Y) while the preview is in flight - the "All" restore
    // below has always done this, a single-tile restore did not.
    const int gen {mSrcGen};
    // one items writer shared by the preview + the restore, so both request the identical selection.
    auto writeRestoreItems = [key, rels](MadJson::Writer& w) {
        w.Key("items");
        w.StartArray();
        for (const std::string& rel : rels) {
            w.StartObject();
            w.Key("system");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            w.Key("id");
            w.String(rel.c_str(), static_cast<rapidjson::SizeType>(rel.length()));
            w.EndObject();
        }
        w.EndArray();
    };
    mRestorePreviewing = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.restore_preview",
        [this, source, writeRestoreItems](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String(mParams.category.c_str(),
                     static_cast<rapidjson::SizeType>(mParams.category.length()));
            writeRestoreItems(w);
        },
        [this, alive, gen, source, cloud, writeRestoreItems](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mRestorePreviewing = false;
            if (gen != mSrcGen)
                return; // the source changed while previewing - don't confirm/restore the stale source
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
            auto start = [this, source, cloud, writeRestoreItems] {
                if (mRunning)
                    return;
                clearRunStream();
                mRunning = true;
                footer()->setStatus(mParams.restoreStatus);
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore",
                    [this, source, writeRestoreItems](MadJson::Writer& w) {
                        w.Key("source");
                        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
                        w.Key("category");
                        w.String(mParams.category.c_str(),
                                 static_cast<rapidjson::SizeType>(mParams.category.length()));
                        writeRestoreItems(w);
                    },
                    [this, a2, cloud](bool ok2, const rapidjson::Value& payload2) {
                        if (a2.expired())
                            return;
                        if (!ok2) {
                            mRunning = false;
                            footer()->setStatus("");
                            footer()->flash(
                                restoreStartErrorText(MadJson::getString(payload2, "message", "error")),
                                restoreStartErrorMs(), true);
                            return;
                        }
                        attachRunStream(MadJson::getString(payload2, "stream"), /*restore=*/true, cloud);
                    },
                    30000);
            };
            if (replace > 0) {
                std::weak_ptr<int> a3 {pageAlive()};
                mWindow->pushGui(new MadMsgBox(
                    std::to_string(replace) + mParams.itemNoun +
                        " already on disk will be REPLACED. A recoverable copy is saved aside first. "
                        "Continue?",
                    "YES", [a3, start] { if (!a3.expired()) start(); }, "CANCEL", nullptr));
            }
            else {
                start();
            }
        },
        20000);
}

// ── the "All" tile ───────────────────────────────────────────────────────────

void MadBackupTilePage::backupAll()
{
    if (mRunning || mPreflight)
        return;
    // Fetch the accurate total first, then a size-aware confirm before pulling data.
    mPreflight = true; // guard a second All-tile press until the confirm modal takes over
    const bool cloud {mCloud};
    const std::string dest {mDest};
    footer()->setStatus("Calculating size...");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_all_size",
        [this](MadJson::Writer& w) {
            w.Key("category");
            w.String(mParams.category.c_str(),
                     static_cast<rapidjson::SizeType>(mParams.category.length()));
        },
        [this, alive, cloud, dest](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mPreflight = false; // the modal is about to take over (grid inert); release the guard
            footer()->setStatus("");
            const long long bytes {ok ? MadJson::getInt64(payload, "size", 0) : 0};
            const std::string where {cloud ? "MEGA" : "On this Deck"};
            std::weak_ptr<int> a2 {pageAlive()};
            mWindow->pushGui(new MadMsgBox(
                "Back up " + mTileParams.allConfirmNoun + " (" + MadPageUtil::humanSize(bytes) +
                    mTileParams.allConfirmExtra + ") to " + where + "? This can take " +
                    mTileParams.allDurationWord + ".",
                "YES",
                [this, a2, cloud, dest] {
                    if (a2.expired() || mRunning)
                        return;
                    if (!cloud) {
                        clearRunStream();
                        mRunning = true;
                        footer()->setStatus("Backing up " + mParams.noun + "...");
                        std::weak_ptr<int> a3 {pageAlive()};
                        pageRequest(
                            mTileParams.backupAllRpc,
                            [dest](MadJson::Writer& w) {
                                if (!dest.empty()) {
                                    w.Key("dest");
                                    w.String(dest.c_str(),
                                             static_cast<rapidjson::SizeType>(dest.length()));
                                }
                            },
                            [this, a3](bool ok2, const rapidjson::Value& p2) {
                                if (a3.expired())
                                    return;
                                if (!ok2) {
                                    mRunning = false;
                                    footer()->setStatus("");
                                    footer()->flash("Couldn't start backup: " +
                                                        MadJson::getString(p2, "message", "error"),
                                                    5000, true);
                                    return;
                                }
                                attachRunStream(MadJson::getString(p2, "stream"), /*restore=*/false,
                                                /*cloud=*/false);
                            },
                            30000);
                        return;
                    }
                    // CLOUD: re-check the connection at the moment of upload, then push everything.
                    footer()->setStatus("Checking MEGA...");
                    std::weak_ptr<int> a3 {pageAlive()};
                    pageRequest(
                        "cloud.status", nullptr,
                        [this, a3](bool ok2, const rapidjson::Value& p2) {
                            if (a3.expired())
                                return;
                            footer()->setStatus("");
                            if (!ok2 || !MadJson::getBool(p2, "connected")) {
                                footer()->flash(
                                    "Not connected to MEGA. Run the cloud setup in Desktop Mode first.",
                                    6000, true);
                                return;
                            }
                            if (mRunning)
                                return;
                            clearRunStream();
                            mRunning = true;
                            footer()->setStatus("Uploading " + mParams.noun + " to MEGA...");
                            std::weak_ptr<int> a4 {pageAlive()};
                            pageRequest(
                                mTileParams.cloudPushAllRpc, nullptr,
                                [this, a4](bool ok3, const rapidjson::Value& p3) {
                                    if (a4.expired())
                                        return;
                                    if (!ok3) {
                                        mRunning = false;
                                        footer()->setStatus("");
                                        footer()->flash("Couldn't start upload: " +
                                                            MadJson::getString(p3, "message", "error"),
                                                        6000, true);
                                        return;
                                    }
                                    attachRunStream(MadJson::getString(p3, "stream"), /*restore=*/false,
                                                    /*cloud=*/true);
                                },
                                30000);
                        },
                        30000);
                },
                "CANCEL", nullptr));
        },
        30000);
}

void MadBackupTilePage::restoreAll()
{
    if (mRunning || mRestorePreviewing)
        return;
    const std::string source {mSource};
    const bool cloud {source.rfind("cloud:", 0) == 0};
    const int gen {mSrcGen}; // supersede if the bar's source changes (X/Y) during the async preview
    auto writeParams = [this, source](MadJson::Writer& w) {
        w.Key("source");
        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        w.Key("category");
        w.String(mParams.category.c_str(),
                 static_cast<rapidjson::SizeType>(mParams.category.length()));
    };
    mRestorePreviewing = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.restore_all_preview", writeParams,
        [this, alive, gen, writeParams, cloud](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mRestorePreviewing = false;
            if (gen != mSrcGen)
                return; // the source changed while previewing - don't confirm/restore the stale source
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
            auto start = [this, writeParams, cloud] {
                if (mRunning)
                    return;
                clearRunStream();
                mRunning = true;
                footer()->setStatus(mParams.restoreStatus);
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore_all", writeParams,
                    [this, a2, cloud](bool ok2, const rapidjson::Value& p2) {
                        if (a2.expired())
                            return;
                        if (!ok2) {
                            mRunning = false;
                            footer()->setStatus("");
                            footer()->flash("Couldn't start restore: " +
                                                MadJson::getString(p2, "message", "error"),
                                            5000, true);
                            return;
                        }
                        attachRunStream(MadJson::getString(p2, "stream"), /*restore=*/true, cloud);
                    },
                    30000);
            };
            const std::string body {
                replace > 0 ? std::to_string(replace) + mParams.itemNoun +
                                  " already on disk will be REPLACED. A recoverable copy is saved "
                                  "aside first. Restore " + mTileParams.allConfirmNoun + "?"
                            : "Restore " + mTileParams.allConfirmNoun + " from this backup?"};
            std::weak_ptr<int> a3 {pageAlive()};
            mWindow->pushGui(new MadMsgBox(body, "YES",
                                           [a3, start] { if (!a3.expired()) start(); }, "CANCEL",
                                           nullptr));
        },
        20000);
}

// ── input / focus ────────────────────────────────────────────────────────────

bool MadBackupTilePage::onBackPressed()
{
    if (mRunning) {
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.", 4000,
                        false);
    }
    return false;
}

bool MadBackupTilePage::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("x", input)) {
        toggleCloud();
        return true;
    }
    if (input.value != 0 && config->isMappedTo("y", input)) {
        changeTarget();
        return true;
    }
    return mGrid != nullptr && mGrid->input(config, input);
}

void MadBackupTilePage::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> MadBackupTilePage::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    // X toggles destination/source; Y changes the folder (backup) / picks a backup (restore). In
    // backup+cloud Y has no real action (MEGA has no folder), so it is omitted rather than advertised.
    prompts.emplace_back("x", mCloud ? "on this deck" : "use mega");
    if (!(mBackup && mCloud))
        prompts.emplace_back("y", mBackup ? "folder" : "change backup");
    return prompts;
}

void MadBackupTilePage::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void MadBackupTilePage::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}
