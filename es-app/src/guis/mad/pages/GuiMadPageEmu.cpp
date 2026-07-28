//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmu.cpp  (deck-patches, P7)
//

#include "guis/mad/pages/GuiMadPageEmu.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageEmuFiles.h"
#include "guis/mad/widgets/MadTileGrid.h"

GuiMadPageEmu::GuiMadPageEmu(GuiMadPanel* panel, const std::string& mode, const MadTarget& target)
    : MadPage {panel, mode == "restore" ? "RESTORE EMULATOR CONFIG" : "BACK UP EMULATOR CONFIG"}
    , mMode {mode}
    , mSource {mode == "restore" ? target.source : "live"}
    , mBackup {mode != "restore"}
    , mCloud {target.cloud}
    , mDest {target.dest}
{
}

GuiMadPageEmu::~GuiMadPageEmu()
{
    clearRunStream();
}

void GuiMadPageEmu::build()
{
    fetchEmulators(); // the destination/source was already chosen upstream (GuiMadPageChooseTarget)
}

// ── the per-emulator tiles ─────────────────────────────────────────────────────

void GuiMadPageEmu::fetchEmulators()
{
    setLoadingText("Loading emulators...");
    const std::string source {mSource};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "emucfg.systems",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load emulators: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mEmulators.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "systems")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    Emu e;
                    e.key = MadJson::getString(s, "key");
                    e.label = MadJson::getString(s, "label");
                    e.art = MadJson::getString(s, "art");
                    e.count = MadJson::getInt(s, "count", 0);
                    if (!e.key.empty())
                        mEmulators.push_back(e);
                }
            rebuildEmulators();
        },
        20000);
}

void GuiMadPageEmu::rebuildEmulators()
{
    if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
        removeChild(mGrid.get());
        mGrid.reset();
    }
    if (mEmulators.empty()) {
        setLoadingText(mBackup ? "No emulators with config found." : "This backup has no emulator config.");
        return;
    }
    std::vector<MadTileGrid::Tile> tiles;
    for (const Emu& e : mEmulators) {
        MadTileGrid::Tile t;
        t.key = e.key;
        t.label = e.label;
        // reuse the console art the backend resolved (a representative system), else the emu-config icon.
        t.artPath = !e.art.empty() ? e.art : MadTheme::routerIconPath("backup-emu");
        tiles.emplace_back(t);
    }
    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mGrid->setSize(mViewportSize.x, mViewportSize.y);
    mGrid->setTiles(tiles);
    mGrid->setCursorIndex(mGridCookie);
    mGrid->setOnPick([this](const std::string& key) { onPickEmulator(key); });
    mGrid->onFocusGained();
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageEmu::onPickEmulator(const std::string& key)
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    std::string label {key};
    for (const Emu& e : mEmulators)
        if (e.key == key) {
            label = e.label;
            break;
        }
    // the leaf backs up / restores through THIS durable root, so the op survives a popped leaf.
    mPanel->pushPage(new GuiMadPageEmuFiles(mPanel, this, mSource, key, label, !mBackup));
}

// ── backup ─────────────────────────────────────────────────────────────────────

// Shared backup items writer: [{emulator, group, rel}] - the shape granular.backup_emucfg AND
// cloud.push_emucfg take.
static void writeEmuBackupItems(MadJson::Writer& w, const std::string& emulator,
                                const std::vector<EmuItem>& items)
{
    w.Key("items");
    w.StartArray();
    for (const EmuItem& it : items) {
        w.StartObject();
        w.Key("emulator");
        w.String(emulator.c_str(), static_cast<rapidjson::SizeType>(emulator.length()));
        w.Key("group");
        w.String(it.group.c_str(), static_cast<rapidjson::SizeType>(it.group.length()));
        w.Key("rel");
        w.String(it.rel.c_str(), static_cast<rapidjson::SizeType>(it.rel.length()));
        w.EndObject();
    }
    w.EndArray();
}

void GuiMadPageEmu::startEmuBackup(const std::string& emulator, const std::vector<EmuItem>& items)
{
    if (mRunning)
        return;
    if (mCloud)
        beginEmuBackupCloud(emulator, items);
    else
        beginEmuBackupLocal(emulator, items, mDest);
}

void GuiMadPageEmu::beginEmuBackupLocal(const std::string& emulator, const std::vector<EmuItem>& items,
                                        const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true; // claim synchronously so a re-entrant X sees busy()
    footer()->setStatus("Backing up emulator config...");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_emucfg",
        [emulator, items, dest](MadJson::Writer& w) {
            writeEmuBackupItems(w, emulator, items);
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

void GuiMadPageEmu::beginEmuBackupCloud(const std::string& emulator, const std::vector<EmuItem>& items)
{
    if (mRunning)
        return;
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA...");
    pageRequest(
        "cloud.status", nullptr,
        [this, alive, emulator, items](bool ok, const rapidjson::Value& payload) {
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
            footer()->setStatus("Uploading emulator config to MEGA...");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                "cloud.push_emucfg",
                [emulator, items](MadJson::Writer& w) { writeEmuBackupItems(w, emulator, items); },
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

// ── restore ────────────────────────────────────────────────────────────────────

void GuiMadPageEmu::startEmuRestore(const std::string& emulator, const std::vector<std::string>& rels)
{
    if (mRunning || mRestorePreviewing)
        return;
    const std::string source {mSource};
    // one items writer shared by the preview + the restore, so both request the identical selection.
    auto writeItems = [emulator, rels](MadJson::Writer& w) {
        w.Key("items");
        w.StartArray();
        for (const std::string& rel : rels) {
            w.StartObject();
            w.Key("system");
            w.String(emulator.c_str(), static_cast<rapidjson::SizeType>(emulator.length()));
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
            w.String("emucfg", 6);
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
                footer()->setStatus("Restoring emulator config...");
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore",
                    [source, writeItems](MadJson::Writer& w) {
                        w.Key("source");
                        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
                        w.Key("category");
                        w.String("emucfg", 6);
                        writeItems(w);
                    },
                    [this, a2](bool ok2, const rapidjson::Value& payload2) {
                        if (a2.expired())
                            return;
                        if (!ok2) {
                            mRunning = false;
                            footer()->setStatus("");
                            // EBUSY (the per-emulator guard) surfaces here as its message, e.g.
                            // "close PCSX2 before restoring its config".
                            footer()->flash(MadJson::getString(payload2, "message",
                                                               "Couldn't start restore."),
                                            6000, true);
                            return;
                        }
                        attachRunStream(MadJson::getString(payload2, "stream"), /*restore=*/true);
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

// ── the running op stream ────────────────────────────────────────────────────────

void GuiMadPageEmu::attachRunStream(const std::string& token, bool restore, bool cloud)
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
                std::string msg {"Restored " + std::to_string(restored) + " item(s)"};
                if (replaced > 0)
                    msg += ", " + std::to_string(replaced) + " replaced";
                if (skipped > 0)
                    msg += ", " + std::to_string(skipped) + " skipped";
                footer()->flash(msg + ". The emulator picks it up on its next launch.", 8000, false);
                if (orphaned > 0) {
                    const std::string snap {MadJson::getString(data, "snapshot")};
                    mWindow->pushGui(new MadMsgBox(
                        std::to_string(orphaned) +
                            " item(s) could not be fully restored and their previous copy was moved aside "
                            "for safety. Roll them back from RECOVERY.txt in:\n\n" +
                            snap,
                        "OK", [] {}));
                }
            }
            else if (cloud) {
                footer()->flash("Backed up emulator config to MEGA.", 8000, false);
            }
            else {
                const int copied {MadJson::getInt(data, "copied", 0)};
                footer()->flash("Backed up " + std::to_string(copied) + " file(s).", 8000, false);
            }
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty())
            footer()->setStatus(line);
    });
}

void GuiMadPageEmu::clearRunStream()
{
    if (!mRunToken.empty()) {
        backend()->clearStreamCallback(mRunToken);
        mRunToken.clear();
    }
}

// ── input / focus ────────────────────────────────────────────────────────────────

bool GuiMadPageEmu::onBackPressed()
{
    if (mRunning) {
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.", 4000,
                        false);
    }
    return false;
}

bool GuiMadPageEmu::input(InputConfig* config, Input input)
{
    return mGrid != nullptr && mGrid->input(config, input);
}

void GuiMadPageEmu::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageEmu::getHelpPrompts()
{
    return mGrid != nullptr ? mGrid->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageEmu::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void GuiMadPageEmu::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}
