//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupFlowPage.cpp  (deck-patches)
//
//  The one copy of the backup / restore flow. See the header for what belongs
//  here and what stays with the pages.
//

#include "guis/mad/MadBackupFlowPage.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadFolderPicker.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (the "change backup" list)

#include <tuple>

MadBackupFlowPage::MadBackupFlowPage(GuiMadPanel* panel, const std::string& title,
                                     const std::string& mode, const Params& params)
    : MadPage {panel, title}
    , mParams {params}
    , mMode {mode}
    , mSource {mode == "restore" ? "" : "live"}
    , mBackup {mode != "restore"}
{
}

void MadBackupFlowPage::resolveDefaultDestination()
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
} // namespace

// ── the destination / source bar ─────────────────────────────────────────────

void MadBackupFlowPage::ensureBar()
{
    if (mBar != nullptr)
        return;
    mBar = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                           MadTheme::color(MadColor::Title), ALIGN_LEFT, ALIGN_CENTER,
                                           glm::ivec2 {1, 1});
    mBar->setPosition(mViewportPos.x, mViewportPos.y);
    addChild(mBar.get());
    refreshBar();
}

std::string MadBackupFlowPage::barText() const
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

void MadBackupFlowPage::refreshBar()
{
    if (mBar != nullptr)
        mBar->setText(barText());
}

void MadBackupFlowPage::toggleCloud()
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

void MadBackupFlowPage::changeTarget()
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

void MadBackupFlowPage::resolveDefaultSource()
{
    mHasSource = false;
    mSource.clear();
    teardownContent();
    const int gen {++mSrcGen};
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
            teardownContent(); // nothing resolved: the previous source's content must not linger
            setLoadingText(cloud ? "No " + mParams.sourceNoun + " backup on MEGA yet."
                                 : std::string {"No local backup found. Press Y to browse."});
            refreshBar();
            mPanel->refreshHelpPrompts(); // the X label branches on mCloud - refresh it in the empty state too
            return;
        }
        applySource(id, created, count);
    };
    if (mCloud) {
        pageRequest(mParams.cloudSourcesRpc, nullptr,
                    [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, true); }, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [cat = mParams.category](MadJson::Writer& w) {
                w.Key("category");
                w.String(cat.c_str(), static_cast<rapidjson::SizeType>(cat.length()));
            },
            [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, false); }, 10000);
    }
    refreshBar();
}

void MadBackupFlowPage::applySource(const std::string& id, const std::string& created, int count)
{
    ++mSrcGen;
    mSource = id;
    mSrcCreated = created;
    mSrcCount = count;
    mHasSource = true;
    refreshBar();
    refetchForSource();
}

void MadBackupFlowPage::openSourcePicker()
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
            footer()->flash("No " + mParams.sourceNoun + " backup on MEGA yet.", 3500, false);
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
        pageRequest(mParams.cloudSourcesRpc, nullptr, present, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [cat = mParams.category](MadJson::Writer& w) {
                w.Key("category");
                w.String(cat.c_str(), static_cast<rapidjson::SizeType>(cat.length()));
            },
            present, 10000);
    }
}

void MadBackupFlowPage::browseForSource()
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
                [path, cat = mParams.category](MadJson::Writer& w) {
                    w.Key("path");
                    w.String(path.c_str(), static_cast<rapidjson::SizeType>(path.length()));
                    w.Key("category");
                    w.String(cat.c_str(), static_cast<rapidjson::SizeType>(cat.length()));
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
                        footer()->flash("No " + mParams.sourceNoun + " backup found in that folder.", 3500, false);
                        return;
                    }
                    mCloud = false;
                    applySource(id, created, count);
                },
                60000);
        },
        "PICK A BACKUP FOLDER"));
}

// ── the run stream ───────────────────────────────────────────────────────────

void MadBackupFlowPage::onRestoreSucceeded(const rapidjson::Value& data)
{
    const int restored {MadJson::getInt(data, "restored", 0)};
    const int replaced {MadJson::getInt(data, "replaced", 0)};
    const int skipped {MadJson::getInt(data, "skipped", 0)};
    int orphaned {0};
    const rapidjson::Value& orphans {MadJson::getMember(data, "orphaned")};
    if (orphans.IsArray())
        orphaned = static_cast<int>(orphans.Size());
    std::string msg {"Restored " + std::to_string(restored) + mParams.itemNoun};
    if (replaced > 0)
        msg += ", " + std::to_string(replaced) + " replaced";
    if (skipped > 0)
        msg += ", " + std::to_string(skipped) + " skipped";
    msg += ".";
    const std::string suffix {restoreDoneSuffix()};
    if (!suffix.empty())
        msg += " " + suffix;
    footer()->flash(msg, 9000, false);
    if (orphaned > 0) {
        const std::string snap {MadJson::getString(data, "snapshot")};
        mWindow->pushGui(new MadMsgBox(
            std::to_string(orphaned) + mParams.itemNoun +
                " could not be fully restored and their previous copy was moved aside "
                "for safety. Roll them back from RECOVERY.txt in:\n\n" + snap,
            "OK", [] {}));
    }
}

void MadBackupFlowPage::attachRunStream(const std::string& token, bool restore, bool cloud)
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
                return;
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
                onRestoreSucceeded(data);
            }
            else if (cloud) {
                const int failed {MadJson::getInt(data, "failed", 0)};
                if (failed > 0)
                    footer()->flash("Backed up to MEGA, but " + std::to_string(failed) +
                                        " file(s) failed to upload. Check the log.",
                                    9000, true);
                else
                    footer()->flash("Backed up " + mParams.noun + " to MEGA.", 8000, false);
            }
            else {
                const int copied {MadJson::getInt(data, "copied", 0)};
                footer()->flash("Backed up " + std::to_string(copied) + mParams.itemNoun + ".", 8000,
                                false);
            }
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty())
            footer()->setStatus(line);
    });
}

void MadBackupFlowPage::clearRunStream()
{
    if (!mRunToken.empty()) {
        backend()->clearStreamCallback(mRunToken);
        mRunToken.clear();
    }
}
