//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageBios.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBiosFiles.h"
#include "guis/mad/widgets/MadTileGrid.h"

GuiMadPageBios::GuiMadPageBios(GuiMadPanel* panel, const std::string& mode, const MadTarget& target)
    : MadPage {panel, mode == "restore" ? "RESTORE BIOS" : "BACK UP BIOS"}
    , mMode {mode}
    , mSource {mode == "restore" ? target.source : "live"}
    , mBackup {mode != "restore"}
    , mCloud {target.cloud}
    , mDest {target.dest}
{
}

GuiMadPageBios::~GuiMadPageBios()
{
    clearRunStream();
}

void GuiMadPageBios::build()
{
    fetchSystems(); // the destination/source was already chosen upstream (GuiMadPageChooseTarget)
}

void GuiMadPageBios::fetchSystems()
{
    setLoadingText("Loading BIOS…");
    const std::string source {mSource};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "bios.systems",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load BIOS: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mBuckets.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "systems")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    Bucket b;
                    b.key = MadJson::getString(s, "key");
                    b.label = MadJson::getString(s, "label");
                    b.art = MadJson::getString(s, "art");
                    b.count = MadJson::getInt(s, "count", 0);
                    if (!b.key.empty())
                        mBuckets.push_back(b);
                }
            rebuildSystems();
        },
        20000);
}

void GuiMadPageBios::rebuildSystems()
{
    if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
        removeChild(mGrid.get());
        mGrid.reset();
    }
    if (mBuckets.empty()) {
        footer()->setStatus(mBackup ? "No BIOS files found." : "This backup has no BIOS.", false);
        return;
    }
    std::vector<MadTileGrid::Tile> tiles;
    for (const Bucket& b : mBuckets) {
        MadTileGrid::Tile t;
        t.key = b.key;
        t.label = b.label;
        // reuse the console art the backend resolved; else the dedicated "Other" icon, else the BIOS icon.
        t.artPath = !b.art.empty() ? b.art
                    : b.key == "other" ? MadTheme::routerIconPath("backup-other-bios")
                                       : MadTheme::routerIconPath("backup-bios");
        tiles.emplace_back(t);
    }
    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mGrid->setSize(mViewportSize.x, mViewportSize.y);
    mGrid->setTiles(tiles);
    mGrid->setCursorIndex(mGridCookie);
    mGrid->setOnPick([this](const std::string& key) { onPickBucket(key); });
    mGrid->onFocusGained();
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBios::onPickBucket(const std::string& key)
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    std::string label {key};
    for (const Bucket& b : mBuckets)
        if (b.key == key) {
            label = b.label;
            break;
        }
    // the leaf backs up / restores through THIS durable root, so the op survives a popped leaf.
    mPanel->pushPage(new GuiMadPageBiosFiles(mPanel, this, mSource, key, label, !mBackup));
}

void GuiMadPageBios::startBiosBackup(const std::string& bucket, const std::vector<std::string>& rels)
{
    if (mRunning)
        return;
    // The destination was chosen upstream (GuiMadPageChooseTarget): a resolved MEGA target or a local
    // folder. mRunning is claimed inside each branch, only once the real backup actually starts.
    if (mCloud)
        beginBiosBackupCloud(bucket, rels);
    else
        beginBiosBackupLocal(bucket, rels, mDest);
}

// Shared items writer: [{bucket, rel}] - the exact shape granular.backup_bios AND cloud.push_bios take.
static void writeBiosItems(MadJson::Writer& w, const std::string& bucket,
                           const std::vector<std::string>& rels)
{
    w.Key("items");
    w.StartArray();
    for (const std::string& rel : rels) {
        w.StartObject();
        w.Key("bucket");
        w.String(bucket.c_str(), static_cast<rapidjson::SizeType>(bucket.length()));
        w.Key("rel");
        w.String(rel.c_str(), static_cast<rapidjson::SizeType>(rel.length()));
        w.EndObject();
    }
    w.EndArray();
}

void GuiMadPageBios::beginBiosBackupLocal(const std::string& bucket,
                                          const std::vector<std::string>& rels, const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true; // claim synchronously so a re-entrant X sees busy()
    footer()->setStatus("Backing up BIOS…");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_bios",
        [bucket, rels, dest](MadJson::Writer& w) {
            writeBiosItems(w, bucket, rels);
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
            attachRunStream(MadJson::getString(payload, "stream"), /*restore=*/false, /*cloud=*/false);
        },
        30000);
}

void GuiMadPageBios::beginBiosBackupCloud(const std::string& bucket,
                                          const std::vector<std::string>& rels)
{
    if (mRunning)
        return;
    // Re-check the connection at the leaf: it may have dropped between the chooser's fail-fast check and
    // now. Firing cloud.push_bios while disconnected would only produce a bare failure AND leave an
    // auto-resume marker replaying a doomed op on every backend start. cloud.status is fast; mRunning is
    // claimed only once connected.
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA…");
    pageRequest(
        "cloud.status", nullptr,
        [this, alive, bucket, rels](bool ok, const rapidjson::Value& payload) {
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
            footer()->setStatus("Uploading BIOS to MEGA…");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                "cloud.push_bios",
                [bucket, rels](MadJson::Writer& w) { writeBiosItems(w, bucket, rels); },
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

void GuiMadPageBios::startBiosRestore(const std::string& bucket, const std::vector<std::string>& rels)
{
    if (mRunning || mRestorePreviewing)
        return;
    const std::string source {mSource};
    // one items writer shared by the preview + the restore, so both request the identical selection.
    auto writeItems = [bucket, rels](MadJson::Writer& w) {
        w.Key("items");
        w.StartArray();
        for (const std::string& rel : rels) {
            w.StartObject();
            w.Key("system");
            w.String(bucket.c_str(), static_cast<rapidjson::SizeType>(bucket.length()));
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
        [source, writeItems](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String("bios", 4);
            writeItems(w);
        },
        [this, alive, source, writeItems](bool ok, const rapidjson::Value& payload) {
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
            auto start = [this, source, writeItems] {
                if (mRunning)
                    return;
                clearRunStream();
                mRunning = true;
                footer()->setStatus("Restoring BIOS…");
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore",
                    [source, writeItems](MadJson::Writer& w) {
                        w.Key("source");
                        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
                        w.Key("category");
                        w.String("bios", 4);
                        writeItems(w);
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
                        attachRunStream(MadJson::getString(payload2, "stream"), /*restore=*/true);
                    },
                    30000);
            };
            if (replace > 0) {
                std::weak_ptr<int> a3 {pageAlive()};
                mWindow->pushGui(new MadMsgBox(
                    std::to_string(replace) + " BIOS file(s) already on disk will be REPLACED. A "
                    "recoverable copy is saved aside first. Continue?",
                    "YES", [a3, start] { if (!a3.expired()) start(); }, "CANCEL", nullptr));
            }
            else {
                start();
            }
        },
        20000);
}

void GuiMadPageBios::attachRunStream(const std::string& token, bool restore, bool cloud)
{
    if (token.empty()) {
        mRunning = false;
        footer()->setStatus("");
        footer()->flash(std::string(restore ? "Restore" : "Backup") + " didn't start.", 5000, true);
        return;
    }
    mRunToken = token;
    std::weak_ptr<int> alive {pageAlive()};
    backend()->setStreamCallback(token, [this, alive, restore, cloud](const rapidjson::Value& data) {
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
                return; // idempotent on a duplicate terminal
            mRunning = false;
            footer()->setStatus("");
            const int rc {MadJson::getInt(data, "rc", -1)};
            if (rc != 0) {
                if (MadJson::getBool(data, "stopped"))
                    footer()->flash("Cancelled.", 6000, true);
                else
                    footer()->flash(std::string(restore ? "Restore failed" : "Backup failed") + ".",
                                    8000, true);
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
                std::string msg {"Restored " + std::to_string(restored) + " BIOS file(s)"};
                if (replaced > 0)
                    msg += ", " + std::to_string(replaced) + " replaced";
                if (skipped > 0)
                    msg += ", " + std::to_string(skipped) + " skipped";
                footer()->flash(msg + ".", 8000, false);
                if (orphaned > 0) {
                    const std::string snap {MadJson::getString(data, "snapshot")};
                    mWindow->pushGui(new MadMsgBox(
                        std::to_string(orphaned) +
                            " BIOS file(s) could not be fully restored and their previous copy was moved "
                            "aside for safety. Roll them back from RECOVERY.txt in:\n\n" +
                            snap,
                        "OK", [] {}));
                }
            }
            else if (cloud) {
                // the cloud stream reports no local file count; the manifest published to MEGA is the proof.
                footer()->flash("Backed up BIOS to MEGA.", 8000, false);
            }
            else {
                const int copied {MadJson::getInt(data, "copied", 0)};
                footer()->flash("Backed up " + std::to_string(copied) + " BIOS file(s).", 8000, false);
            }
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty())
            footer()->setStatus(line);
    });
}

void GuiMadPageBios::clearRunStream()
{
    if (!mRunToken.empty()) {
        backend()->clearStreamCallback(mRunToken);
        mRunToken.clear();
    }
}

// ── input / focus ────────────────────────────────────────────────────────────

bool GuiMadPageBios::onBackPressed()
{
    if (mRunning) {
        // Leaving is allowed: the daemon op keeps running (a cloud transfer is adopted by the Backup
        // Landing's "Transfers" tile; a local copy finishes on its own, rule-5 safe). Flash so the user
        // knows B did not cancel it. clearRunStream() (dtor) just detaches this page's listener.
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") +
                            " in the background.",
                        4000, false);
    }
    return false;
}

bool GuiMadPageBios::input(InputConfig* config, Input input)
{
    return mGrid != nullptr && mGrid->input(config, input);
}

void GuiMadPageBios::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageBios::getHelpPrompts()
{
    return mGrid != nullptr ? mGrid->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageBios::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void GuiMadPageBios::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}
