//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmu.cpp  (deck-patches, P7)
//

#include "guis/mad/pages/GuiMadPageEmu.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadFolderPicker.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (the restore "change backup" list)
#include "guis/mad/pages/GuiMadPageEmuFiles.h"
#include "guis/mad/widgets/MadTileGrid.h"
#include "guis/mad/MadPageUtil.h"

#include <cstdio>
#include <tuple>

namespace
{
    std::string fmtWhen(const std::string& created)
    {
        if (created.size() == 15 && created[8] == 'T')
            return created.substr(0, 4) + "-" + created.substr(4, 2) + "-" + created.substr(6, 2) + " " +
                   created.substr(9, 2) + ":" + created.substr(11, 2);
        return created;
    }
    std::string shortPath(const std::string& p)
    {
        if (p.size() <= 42)
            return p;
        return "..." + p.substr(p.size() - 39);
    }
    constexpr const char* kBrowseSentinel {"\x01""browse"};
    constexpr const char* kCloudRpc {"emucfg.cloud_sources"};
    constexpr const char* kCategory {"emucfg"};
    constexpr rapidjson::SizeType kCategoryLen {6};
    constexpr const char* kAllSentinel {"\x01""all"}; // the leading "All emulators" grid tile
}

GuiMadPageEmu::GuiMadPageEmu(GuiMadPanel* panel, const std::string& mode)
    : MadPage {panel, mode == "restore" ? "RESTORE EMULATOR CONFIG" : "BACK UP EMULATOR CONFIG"}
    , mMode {mode}
    , mSource {mode == "restore" ? "" : "live"}
    , mBackup {mode != "restore"}
{
}

GuiMadPageEmu::~GuiMadPageEmu()
{
    clearRunStream();
}

void GuiMadPageEmu::build()
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
        fetchEmulators();
        resolveDefaultDestination();
    }
    else {
        resolveDefaultDestination(); // probe first, then resolve the latest cloud/local backup once
    }
}

void GuiMadPageEmu::resolveDefaultDestination()
{
    // Decide the default destination (backup) / source (restore) via a cloud.status probe FIRST,
    // so the bar + grid never flash On-this-Deck before switching to MEGA. Default to MEGA when it
    // is configured; a Deck with no MEGA (or a failed probe) stays On-this-Deck. A manual X/Y during
    // the probe (mDestTouched) wins. The bar reads "(checking...)" until this resolves.
    if (mDestResolved)
        return;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest("cloud.status", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired())
                        return;
                    mDestResolved = true;
                    if (mDestTouched) { // the user chose during the probe; their action did the resolve
                        refreshBar();
                        return;
                    }
                    if (ok && MadJson::getBool(payload, "connected"))
                        mCloud = true; // default to MEGA when it is available
                    if (mBackup) {
                        refreshBar();
                        mPanel->refreshHelpPrompts();
                    }
                    else {
                        resolveDefaultSource(); // resolve the RIGHT source once (cloud if connected)
                    }
                },
                30000);
}

// ── the destination / source bar ─────────────────────────────────────────────

void GuiMadPageEmu::ensureBar()
{
    if (mBar != nullptr)
        return;
    // {1,1} = auto-size width AND height -> a single-line label sized to its text (no wrap into the grid).
    mBar = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                           MadTheme::color(MadColor::Title), ALIGN_LEFT, ALIGN_CENTER,
                                           glm::ivec2 {1, 1});
    mBar->setPosition(mViewportPos.x, mViewportPos.y);
    addChild(mBar.get());
    refreshBar();
}

std::string GuiMadPageEmu::barText() const
{
    if (!mDestResolved)
        return mBackup ? "Save to:  (checking...)" : "Restore from:  (checking...)";
    // The X/Y hints live in the footer help row (getHelpPrompts), not in this bar.
    if (mBackup) {
        if (mCloud)
            return "Save to:  MEGA cloud";
        const std::string dest {mDest.empty() ? "(loading...)" : shortPath(mDest)};
        return "Save to:  On this Deck  " + dest;
    }
    const std::string label {mHasSource ? fmtWhen(mSrcCreated) + "  (" + std::to_string(mSrcCount) +
                                              (mSrcCount == 1 ? " file)" : " files)")
                                        : "(no backup found)"};
    if (mCloud)
        return "Restore from:  MEGA  " + label;
    return "Restore from:  On this Deck  " + label;
}

void GuiMadPageEmu::refreshBar()
{
    if (mBar != nullptr)
        mBar->setText(barText());
}

void GuiMadPageEmu::toggleCloud()
{
    if (mChecking)
        return;
    mDestTouched = true;  // a manual X-press latches the destination choice
    mDestResolved = true; // ...and shows the real bar (never leave it at "(checking...)")
    if (!mCloud) {
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
                mPanel->refreshHelpPrompts(); // the X/Y labels branch on mCloud
            },
            30000);
        return;
    }
    mCloud = false;
    if (!mBackup)
        resolveDefaultSource();
    refreshBar();
    mPanel->refreshHelpPrompts(); // the X/Y labels branch on mCloud
}

void GuiMadPageEmu::changeTarget()
{
    mDestTouched = true;  // a manual Y-press latches the destination choice
    mDestResolved = true; // ...and shows the real bar (never leave it at "(checking...)")
    if (mBackup) {
        if (mCloud) {
            footer()->flash("MEGA has no folder to pick - X switches back to On this Deck.", 3500, false);
            return;
        }
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
    openSourcePicker();
}

// ── restore: resolve / pick a source ─────────────────────────────────────────

void GuiMadPageEmu::resolveDefaultSource()
{
    mHasSource = false;
    // Tear down the OLD source's grid + clear mSource NOW (synchronously), before the possibly-slow fetch,
    // so the previous kind's tiles can't be drilled into (and restored from) while the new source loads.
    mSource.clear();
    mEmulators.clear();
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
        mPanel->refreshHelpPrompts();
    }
    const int gen {++mSrcGen}; // a later toggle / pick bumps mSrcGen and supersedes this resolve
    setLoadingText(mCloud ? "Looking on MEGA..." : "Looking for backups...");
    std::weak_ptr<int> alive {pageAlive()};
    auto handle = [this, alive, gen](bool ok, const rapidjson::Value& payload, bool cloud) {
        if (alive.expired() || gen != mSrcGen)
            return;
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
            mEmulators.clear();
            if (mGrid != nullptr) {
                removeChild(mGrid.get());
                mGrid.reset();
            }
            setLoadingText(cloud ? "No emulator-config backup on MEGA yet."
                                 : "No local backup found. Press Y to browse.");
            refreshBar();
            mPanel->refreshHelpPrompts(); // the X label branches on mCloud - refresh it in the empty state too
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
            [](MadJson::Writer& w) {
                w.Key("category");
                w.String(kCategory, kCategoryLen);
            },
            [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, false); }, 10000);
    }
    refreshBar();
}

void GuiMadPageEmu::applySource(const std::string& id, const std::string& created, int count)
{
    ++mSrcGen; // supersede any in-flight resolve so it can't overwrite this (user-picked) source
    mSource = id;
    mSrcCreated = created;
    mSrcCount = count;
    mHasSource = true;
    refreshBar();
    fetchEmulators();
}

void GuiMadPageEmu::openSourcePicker()
{
    const bool cloud {mCloud};
    footer()->setStatus(cloud ? "Looking on MEGA..." : "Looking for backups...");
    std::weak_ptr<int> alive {pageAlive()};
    const std::string current {mSource};
    auto present = [this, alive, cloud, current](bool ok, const rapidjson::Value& payload) {
        if (alive.expired())
            return;
        footer()->setStatus("");
        std::vector<std::pair<std::string, std::string>> options;
        std::vector<std::tuple<std::string, std::string, int>> srcs;
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
                                                 (cnt == 1 ? " file" : " files"));
                    srcs.emplace_back(id, cr, cnt);
                }
        }
        if (cloud && srcs.empty()) {
            footer()->flash("No emulator-config backup on MEGA yet.", 3500, false);
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
            [](MadJson::Writer& w) {
                w.Key("category");
                w.String(kCategory, kCategoryLen);
            },
            present, 10000);
    }
}

void GuiMadPageEmu::browseForSource()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadFolderPicker(
        [this, alive](const std::string& path) {
            if (alive.expired() || path.empty())
                return;
            footer()->setStatus("Looking for backups...");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                "granular.sources_under",
                [path](MadJson::Writer& w) {
                    w.Key("path");
                    w.String(path.c_str(), static_cast<rapidjson::SizeType>(path.length()));
                    w.Key("category");
                    w.String(kCategory, kCategoryLen);
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
                            const rapidjson::Value& s {arr[0]};
                            id = MadJson::getString(s, "id");
                            created = MadJson::getString(s, "created");
                            count = MadJson::getInt(s, "count", 0);
                        }
                    }
                    if (id.empty()) {
                        footer()->flash("No emulator-config backup found in that folder.", 3500, false);
                        return;
                    }
                    mCloud = false;
                    applySource(id, created, count);
                },
                60000);
        },
        "PICK A BACKUP FOLDER"));
}

// ── the per-emulator tiles ─────────────────────────────────────────────────────

void GuiMadPageEmu::fetchEmulators()
{
    setLoadingText("Loading emulators...");
    const std::string source {mSource};
    const int gen {mSrcGen}; // a newer source pick supersedes this fetch (restore); no-op for backup
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "emucfg.systems",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive, gen](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || gen != mSrcGen)
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
    const float barH {Font::get(FONT_SIZE_SMALL)->getHeight() * 1.8f};
    std::vector<MadTileGrid::Tile> tiles;
    {   // a leading "All" tile: back up / restore EVERY emulator at once (size-aware confirm; incl. big dirs).
        MadTileGrid::Tile all;
        all.key = kAllSentinel;
        all.label = "All";
        all.artPath = MadTheme::routerIconPath("backup-emu");
        tiles.push_back(all);
    }
    for (const Emu& e : mEmulators) {
        MadTileGrid::Tile t;
        t.key = e.key;
        t.label = e.label;
        // reuse the console art the backend resolved (a representative system), else the emu-config icon.
        t.artPath = !e.art.empty() ? e.art : MadTheme::routerIconPath("backup-emu");
        tiles.emplace_back(t);
    }
    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y + barH);
    mGrid->setSize(mViewportSize.x, mViewportSize.y - barH);
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
    if (key == kAllSentinel) { // the leading "All" tile: every emulator at once
        if (mBackup)
            backupAllEmu();
        else
            restoreAllEmu();
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
    // The destination is whatever the bar shows (MEGA or the remembered local folder).
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
    // A "cloud:" source id means the restore is pulling from MEGA; family A computed this
    // and passed it to the runner, this family never did.
    const bool cloud {source.rfind("cloud:", 0) == 0};
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
            w.String(kCategory, kCategoryLen);
            writeItems(w);
        },
        [this, alive, source, writeItems, cloud](bool ok, const rapidjson::Value& payload) {
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
            auto start = [this, source, writeItems, cloud] {
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
                        w.String(kCategory, kCategoryLen);
                        writeItems(w);
                    },
                    [this, a2, cloud](bool ok2, const rapidjson::Value& payload2) {
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
                        attachRunStream(MadJson::getString(payload2, "stream"), /*restore=*/true, cloud);
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

void GuiMadPageEmu::backupAllEmu()
{
    if (mRunning || mPreflight)
        return;
    // Walk the WHOLE set (incl. the giant texture/mod/NAND/HDD dirs the tiles omit), then a size-aware confirm.
    mPreflight = true; // guard a second All-tile press during the slow size walk, until the confirm takes over
    const bool cloud {mCloud};
    const std::string dest {mDest};
    footer()->setStatus("Calculating size...");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_all_size",
        [](MadJson::Writer& w) { w.Key("category"); w.String(kCategory, kCategoryLen); },
        [this, alive, cloud, dest](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mPreflight = false; // the modal is about to take over (grid inert); release the preflight guard
            footer()->setStatus("");
            const long long bytes {ok ? MadJson::getInt64(payload, "size", 0) : 0};
            const std::string where {cloud ? "MEGA" : "On this Deck"};
            std::weak_ptr<int> a2 {pageAlive()};
            mWindow->pushGui(new MadMsgBox(
                "Back up ALL emulator config (" + MadPageUtil::humanSize(bytes) +
                    ", includes texture/mod packs) to " + where + "? This can take a long time.",
                "YES",
                [this, a2, cloud, dest] {
                    if (a2.expired() || mRunning)
                        return;
                    if (!cloud) {
                        clearRunStream();
                        mRunning = true;
                        footer()->setStatus("Backing up emulator config...");
                        std::weak_ptr<int> a3 {pageAlive()};
                        pageRequest(
                            "granular.backup_emucfg_all",
                            [dest](MadJson::Writer& w) {
                                if (!dest.empty()) {
                                    w.Key("dest");
                                    w.String(dest.c_str(), static_cast<rapidjson::SizeType>(dest.length()));
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
                    // CLOUD: re-check the connection, then upload every emulator's config/data.
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
                            footer()->setStatus("Uploading emulator config to MEGA...");
                            std::weak_ptr<int> a4 {pageAlive()};
                            pageRequest(
                                "cloud.push_emucfg_all", nullptr,
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

void GuiMadPageEmu::restoreAllEmu()
{
    if (mRunning || mRestorePreviewing)
        return;
    const std::string source {mSource};
    // A "cloud:" source id means the restore is pulling from MEGA; family A computed this
    // and passed it to the runner, this family never did.
    const bool cloud {source.rfind("cloud:", 0) == 0};
    const int gen {mSrcGen}; // supersede if the bar's source changes (X/Y) during the async preview
    auto writeParams = [source](MadJson::Writer& w) {
        w.Key("source");
        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        w.Key("category");
        w.String(kCategory, kCategoryLen);
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
                footer()->setStatus("Restoring emulator config...");
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
                replace > 0
                    ? std::to_string(replace) + " item(s) already on disk will be REPLACED. A recoverable "
                      "copy is saved aside first. Restore ALL emulator config?"
                    : "Restore ALL emulator config from this backup?"};
            std::weak_ptr<int> a3 {pageAlive()};
            mWindow->pushGui(new MadMsgBox(body, "YES",
                                          [a3, start] { if (!a3.expired()) start(); }, "CANCEL", nullptr));
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
                const int failed {MadJson::getInt(data, "failed", 0)};
                if (failed > 0)
                    footer()->flash("Backed up to MEGA, but " + std::to_string(failed) +
                                        " file(s) failed to upload. Check the log.",
                                    9000, true);
                else
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

void GuiMadPageEmu::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageEmu::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    prompts.emplace_back("x", mCloud ? "on this deck" : "use mega");
    if (!(mBackup && mCloud))
        prompts.emplace_back("y", mBackup ? "folder" : "change backup");
    return prompts;
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
