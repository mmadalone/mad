//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageBios.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadFolderPicker.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (the restore "change backup" list)
#include "guis/mad/pages/GuiMadPageBiosFiles.h"
#include "guis/mad/widgets/MadTileGrid.h"

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
    constexpr const char* kCloudRpc {"bios.cloud_sources"};
    constexpr const char* kCategory {"bios"};
}

GuiMadPageBios::GuiMadPageBios(GuiMadPanel* panel, const std::string& mode)
    : MadPage {panel, mode == "restore" ? "RESTORE BIOS" : "BACK UP BIOS"}
    , mMode {mode}
    , mSource {mode == "restore" ? "" : "live"}
    , mBackup {mode != "restore"}
{
}

GuiMadPageBios::~GuiMadPageBios()
{
    clearRunStream();
}

void GuiMadPageBios::build()
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
        fetchSystems();
        resolveDefaultDestination();
    }
    else {
        resolveDefaultDestination(); // probe first, then resolve the latest cloud/local backup once
    }
}

void GuiMadPageBios::resolveDefaultDestination()
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

void GuiMadPageBios::ensureBar()
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

std::string GuiMadPageBios::barText() const
{
    if (!mDestResolved)
        return mBackup ? "Save to:  (checking...)" : "Restore from:  (checking...)";
    // The X/Y hints live in the footer help row (getHelpPrompts), not in this bar - the bar carries only
    // the destination/source state.
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

void GuiMadPageBios::refreshBar()
{
    if (mBar != nullptr)
        mBar->setText(barText());
}

void GuiMadPageBios::toggleCloud()
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

void GuiMadPageBios::changeTarget()
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

void GuiMadPageBios::resolveDefaultSource()
{
    mHasSource = false;
    // Tear down the OLD source's grid + clear mSource NOW (synchronously), before the possibly-slow fetch,
    // so the previous kind's buckets can't be drilled into (and restored from) while the new source loads.
    mSource.clear();
    mBuckets.clear();
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
        mPanel->refreshHelpPrompts(); // no grid -> clear its stale prompt during the fetch
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
            mBuckets.clear();
            if (mGrid != nullptr) {
                removeChild(mGrid.get());
                mGrid.reset();
            }
            setLoadingText(cloud ? "No BIOS backup on MEGA yet." : "No local backup found. Press Y to browse.");
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
                w.String(kCategory, 4);
            },
            [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, false); }, 10000);
    }
    refreshBar();
}

void GuiMadPageBios::applySource(const std::string& id, const std::string& created, int count)
{
    ++mSrcGen; // supersede any in-flight resolve so it can't overwrite this (user-picked) source
    mSource = id;
    mSrcCreated = created;
    mSrcCount = count;
    mHasSource = true;
    refreshBar();
    fetchSystems();
}

void GuiMadPageBios::openSourcePicker()
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
            footer()->flash("No BIOS backup on MEGA yet.", 3500, false);
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
                w.String(kCategory, 4);
            },
            present, 10000);
    }
}

void GuiMadPageBios::browseForSource()
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
                    w.String(kCategory, 4);
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
                        footer()->flash("No BIOS backup found in that folder.", 3500, false);
                        return;
                    }
                    mCloud = false;
                    applySource(id, created, count);
                },
                60000);
        },
        "PICK A BACKUP FOLDER"));
}

// ── the bucket tiles ─────────────────────────────────────────────────────────

void GuiMadPageBios::fetchSystems()
{
    setLoadingText("Loading BIOS...");
    const std::string source {mSource};
    const int gen {mSrcGen}; // a newer source pick supersedes this fetch (restore); no-op for backup
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "bios.systems",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive, gen](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || gen != mSrcGen)
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
        setLoadingText(mBackup ? "No BIOS files found." : "This backup has no BIOS.");
        return;
    }
    const float barH {Font::get(FONT_SIZE_SMALL)->getHeight() * 1.8f};
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
    mGrid->setPosition(mViewportPos.x, mViewportPos.y + barH);
    mGrid->setSize(mViewportSize.x, mViewportSize.y - barH);
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
    // The destination is whatever the bar shows (MEGA or the remembered local folder).
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
    footer()->setStatus("Backing up BIOS...");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_bios",
        [bucket, rels, dest](MadJson::Writer& w) {
            writeBiosItems(w, bucket, rels);
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

void GuiMadPageBios::beginBiosBackupCloud(const std::string& bucket,
                                          const std::vector<std::string>& rels)
{
    if (mRunning)
        return;
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA...");
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
            if (mRunning)
                return;
            clearRunStream();
            mRunning = true;
            footer()->setStatus("Uploading BIOS to MEGA...");
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
                footer()->setStatus("Restoring BIOS...");
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
                const int failed {MadJson::getInt(data, "failed", 0)};
                if (failed > 0)
                    footer()->flash("Backed up to MEGA, but " + std::to_string(failed) +
                                        " file(s) failed to upload. Check the log.",
                                    9000, true);
                else
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
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.", 4000,
                        false);
    }
    return false;
}

bool GuiMadPageBios::input(InputConfig* config, Input input)
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

void GuiMadPageBios::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageBios::getHelpPrompts()
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
