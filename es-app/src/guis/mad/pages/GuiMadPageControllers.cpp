//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageControllers.cpp  (deck-patches, P12a)
//

#include "guis/mad/pages/GuiMadPageControllers.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadFolderPicker.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (the restore "change backup" list)
#include "guis/mad/widgets/MadVirtualList.h"
#include "guis/mad/MadPageUtil.h"

#include <cstdlib>
#include <tuple>

GuiMadPageControllers::GuiMadPageControllers(GuiMadPanel* panel, const std::string& mode)
    : MadPage {panel, mode == "restore" ? "RESTORE CONTROLLER CONFIG" : "BACK UP CONTROLLER CONFIG"}
    , mMode {mode}
    , mSource {mode == "restore" ? "" : "live"}
    , mBackup {mode != "restore"}
{
}

GuiMadPageControllers::~GuiMadPageControllers()
{
    clearRunStream();
}

void GuiMadPageControllers::build()
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
        fetchGroups();
        resolveDefaultDestination();
    }
    else {
        resolveDefaultDestination(); // probe first, then resolve the latest cloud/local backup once
    }
}

void GuiMadPageControllers::resolveDefaultDestination()
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

void GuiMadPageControllers::ensureBar()
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

std::string GuiMadPageControllers::barText() const
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

void GuiMadPageControllers::refreshBar()
{
    if (mBar != nullptr)
        mBar->setText(barText());
}

void GuiMadPageControllers::toggleCloud()
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

void GuiMadPageControllers::changeTarget()
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

void GuiMadPageControllers::resolveDefaultSource()
{
    mHasSource = false;
    mSource.clear();
    mGroups.clear();
    if (mList != nullptr)
        mList->setRows({}, /*keepCursor=*/false);
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
            setLoadingText(cloud ? "No controller-config backup on MEGA yet."
                                 : "No local backup found. Press Y to browse.");
            refreshBar();
            mPanel->refreshHelpPrompts(); // the X label branches on mCloud - refresh it in the empty state too
            return;
        }
        applySource(id, created, count);
    };
    if (mCloud) {
        pageRequest("controllers.cloud_sources", nullptr,
                    [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, true); }, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [](MadJson::Writer& w) {
                w.Key("category");
                w.String("controllers", 11);
            },
            [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, false); }, 10000);
    }
    refreshBar();
}

void GuiMadPageControllers::applySource(const std::string& id, const std::string& created, int count)
{
    ++mSrcGen;
    mSource = id;
    mSrcCreated = created;
    mSrcCount = count;
    mHasSource = true;
    refreshBar();
    fetchGroups();
}

void GuiMadPageControllers::openSourcePicker()
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
            footer()->flash("No controller-config backup on MEGA yet.", 3500, false);
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
        pageRequest("controllers.cloud_sources", nullptr, present, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [](MadJson::Writer& w) {
                w.Key("category");
                w.String("controllers", 11);
            },
            present, 10000);
    }
}

void GuiMadPageControllers::browseForSource()
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
                    w.String("controllers", 11);
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
                        footer()->flash("No controller-config backup found in that folder.", 3500, false);
                        return;
                    }
                    mCloud = false;
                    applySource(id, created, count);
                },
                60000);
        },
        "PICK A BACKUP FOLDER"));
}

bool GuiMadPageControllers::groupTicked(const Group& g) const
{
    return g.selected;
}

// ── the group list ───────────────────────────────────────────────────────────

void GuiMadPageControllers::fetchGroups()
{
    setLoadingText("Loading controller config…");
    const std::string source {mSource};
    const int gen {mSrcGen}; // a newer source pick (the bar) supersedes this fetch; drop a stale, out-of-
                             // order controllers.groups reply so it can't repopulate the group list under a
                             // different source (controllers.groups is slow=True, so replies can arrive reordered).
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "controllers.groups",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive, gen](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || gen != mSrcGen)
                return;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load controller config: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mGroups.clear();
            const rapidjson::Value& arr {MadJson::getMember(payload, "groups")};
            if (arr.IsArray())
                for (const rapidjson::Value& gv : arr.GetArray()) {
                    Group g;
                    g.key = MadJson::getString(gv, "key");
                    g.label = MadJson::getString(gv, "label");
                    g.explain = MadJson::getString(gv, "explain");
                    g.size = MadJson::getInt64(gv, "size", 0);
                    g.present = MadJson::getBool(gv, "present");
                    const rapidjson::Value& fs {MadJson::getMember(gv, "files")};
                    if (fs.IsArray())
                        for (const rapidjson::Value& fv : fs.GetArray()) {
                            File f;
                            f.rel = MadJson::getString(fv, "rel");
                            f.name = MadJson::getString(fv, "name");
                            f.size = MadJson::getInt64(fv, "size", 0);
                            if (!f.rel.empty())
                                g.files.push_back(f);
                        }
                    // default: pre-tick every present group so "back up / restore everything" is one press.
                    g.selected = g.present;
                    if (!g.key.empty())
                        mGroups.push_back(g);
                }
            rebuildGroups();
        },
        20000);
}

void GuiMadPageControllers::ensureWidgets()
{
    if (mList != nullptr)
        return;
    const float listWidth {mViewportSize.x * 0.60f};
    const float headerHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 2.0f};
    const float barH {Font::get(FONT_SIZE_SMALL)->getHeight() * 1.8f}; // room for the dest/source bar on top

    mHeader = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                              ALIGN_CENTER, glm::ivec2 {0, 1});
    mHeader->setPosition(mViewportPos.x, mViewportPos.y + barH);
    mHeader->setSize(listWidth, 0.0f);
    addChild(mHeader.get());

    const float listTop {mViewportPos.y + barH + headerHeight};
    mList = std::make_shared<MadVirtualList>();
    mList->setPosition(mViewportPos.x, listTop);
    mList->setSize(listWidth, mViewportPos.y + mViewportSize.y - listTop);
    mList->setOnSelect([this](int i) { onListSelect(i); });
    mList->setOnCursorChanged([this](int) { updateExplain(); });
    addChild(mList.get());
    mList->onFocusGained();

    // the explanation side pane (the other 40%): a wrapping paragraph for the highlighted group.
    // {0,1} = fixed width (from setSize), auto-calc height -> the paragraph WRAPS within the pane width.
    mExplain = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                               MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                               ALIGN_TOP, glm::ivec2 {0, 1});
    const float paneX {mViewportPos.x + listWidth + mViewportSize.x * 0.03f};
    mExplain->setPosition(paneX, listTop);
    mExplain->setSize(mViewportPos.x + mViewportSize.x - paneX, 0.0f);
    addChild(mExplain.get());
}

std::string GuiMadPageControllers::rowText(const Group& g) const
{
    if (!g.present)
        return "○ " + g.label + "  (none)";
    const std::string glyph {g.selected ? "● " : "○ "};
    return glyph + g.label + "  (" + MadPageUtil::humanSizeCompact(g.size) + ")";
}

void GuiMadPageControllers::rebuildGroups()
{
    ensureWidgets();
    mHeader->setText(headerText());
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mGroups.size() + 1);
    for (const Group& g : mGroups) {
        const unsigned int col {!g.present ? MadTheme::color(MadColor::Secondary)
                                : groupTicked(g) ? MadTheme::color(MadColor::Primary)
                                                 : MadTheme::color(MadColor::Secondary)};
        rows.push_back({rowText(g), col});
    }
    // the action row: A backs up / restores the ticked groups (X/Y drive the location bar).
    rows.push_back({std::string(mBackup ? "> Back up now" : "> Restore now"),
                    MadTheme::color(MadColor::Title)});
    if (!mBackup) {
        // Restore mode also offers the two LOCAL revert ops (not tied to a backup source), kept from the old
        // Controller config page: revert every emulator input to its one-time pre-MAD backup, and reset the
        // routing overrides to defaults.
        rows.push_back({"> Revert emulator inputs to pre-MAD backups", MadTheme::color(MadColor::Red)});
        rows.push_back({"> Reset routing overrides to defaults", MadTheme::color(MadColor::Red)});
    }
    mList->setRows(rows, /*keepCursor=*/true);
    mPanel->refreshHelpPrompts();
    updateExplain();
}

void GuiMadPageControllers::updateExplain()
{
    if (mExplain == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    const int n {static_cast<int>(mGroups.size())};
    if (c == n) { // the action row
        mExplain->setText(mBackup
            ? "Press A to back up the ticked groups to the destination in the bar above."
            : "Press A to restore the ticked groups now (a recoverable copy of any replaced file is kept).");
        return;
    }
    if (!mBackup && c == n + 1) {
        mExplain->setText("Revert EVERY emulator input config to the one-time backup taken before MAD's first "
                          "write (the pre-MAD state). Close any open emulators first.");
        return;
    }
    if (!mBackup && c == n + 2) {
        mExplain->setText("Delete your MAD routing overrides (controller-policy.local.toml) and go back to the "
                          "documented defaults.");
        return;
    }
    mExplain->setText(c >= 0 && c < n ? mGroups[c].explain : "");
}

std::string GuiMadPageControllers::headerText() const
{
    int n {0};
    for (const Group& g : mGroups)
        if (groupTicked(g))
            ++n;
    return std::to_string(n) + " selected  ·  A tick  ·  " +
           (mBackup ? "last row backs up" : "last row restores");
}

void GuiMadPageControllers::onListSelect(int listIndex)
{
    const int n {static_cast<int>(mGroups.size())};
    if (listIndex == n) { // the "Back up / Restore now" row
        act();
        return;
    }
    if (!mBackup && listIndex == n + 1) { // restore-mode revert rows
        revertInputBackups();
        return;
    }
    if (!mBackup && listIndex == n + 2) {
        resetOverrides();
        return;
    }
    toggleAt(listIndex);
}

void GuiMadPageControllers::toggleAt(int groupIndex)
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    if (groupIndex < 0 || groupIndex >= static_cast<int>(mGroups.size()))
        return;
    Group& g {mGroups[groupIndex]};
    if (!g.present) {
        footer()->flash("Nothing to back up in that group.", 2500, false);
        return;
    }
    g.selected = !g.selected;
    rebuildGroups();
}

bool GuiMadPageControllers::anyTicked() const
{
    for (const Group& g : mGroups)
        if (g.selected)
            return true;
    return false;
}

void GuiMadPageControllers::act()
{
    if (mRunning || mRestorePreviewing)
        return;
    if (!anyTicked()) {
        footer()->flash("Tick at least one group first.", 2500, false);
        return;
    }
    if (mBackup) {
        // The destination is whatever the bar shows (X toggles MEGA; Y picks the local folder).
        if (mCloud)
            beginBackupCloud();
        else
            beginBackupLocal(mDest);
    }
    else {
        startRestore();
    }
}

void GuiMadPageControllers::revertInputBackups()
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "Revert EVERY emulator input config to its one-time backup from before MAD's first write? Close any "
        "open emulators first. A recoverable copy is kept.",
        "REVERT",
        [this, alive] {
            if (alive.expired())
                return;
            footer()->setStatus("Reverting input configs...");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest("backup.restore_router", nullptr,
                        [this, a2](bool ok, const rapidjson::Value& p) {
                            if (a2.expired())
                                return;
                            footer()->setStatus("");
                            footer()->flash(ok ? MadJson::getString(p, "message", "Input configs reverted.")
                                               : "Couldn't revert: " +
                                                     MadJson::getString(p, "message", "error"),
                                            4500, !ok);
                        },
                        30000);
        },
        "CANCEL", nullptr));
}

void GuiMadPageControllers::resetOverrides()
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "Delete ALL your MAD routing overrides (controller-policy.local.toml) and revert to the documented "
        "defaults?",
        "RESET",
        [this, alive] {
            if (alive.expired())
                return;
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest("backup.reset_local", nullptr,
                        [this, a2](bool ok, const rapidjson::Value& p) {
                            if (a2.expired())
                                return;
                            footer()->flash(ok ? MadJson::getString(p, "message", "Reset to defaults.")
                                               : "Couldn't reset: " +
                                                     MadJson::getString(p, "message", "error"),
                                            4500, !ok);
                        });
        },
        "CANCEL", nullptr));
}

void GuiMadPageControllers::writeItems(MadJson::Writer& w, bool restore) const
{
    auto one = [&](const std::string& group, const std::string& rel) {
        w.StartObject();
        if (restore) {
            w.Key("system");
            w.String(group.c_str(), static_cast<rapidjson::SizeType>(group.length()));
            w.Key("id");
        }
        else {
            w.Key("group");
            w.String(group.c_str(), static_cast<rapidjson::SizeType>(group.length()));
            w.Key("rel");
        }
        w.String(rel.c_str(), static_cast<rapidjson::SizeType>(rel.length()));
        w.EndObject();
    };
    w.Key("items");
    w.StartArray();
    for (const Group& g : mGroups)
        if (g.selected)
            for (const File& f : g.files)
                one(g.key, f.rel);
    w.EndArray();
}

// ── backup ───────────────────────────────────────────────────────────────────

void GuiMadPageControllers::beginBackupLocal(const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true;
    footer()->setStatus("Backing up controller config…");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_controllers",
        [this, dest](MadJson::Writer& w) {
            writeItems(w, /*restore=*/false);
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

void GuiMadPageControllers::beginBackupCloud()
{
    if (mRunning)
        return;
    // Re-check the connection at the leaf: it may have dropped between the chooser's fail-fast check and now.
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA…");
    pageRequest(
        "cloud.status", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
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
            footer()->setStatus("Uploading controller config to MEGA…");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                "cloud.push_controllers",
                [this](MadJson::Writer& w) { writeItems(w, /*restore=*/false); },
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

// ── live restore ─────────────────────────────────────────────────────────────

void GuiMadPageControllers::startRestore()
{
    if (mRunning || mRestorePreviewing)
        return;
    const std::string source {mSource};
    const bool cloud {source.rfind("cloud:", 0) == 0};
    mRestorePreviewing = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.restore_preview",
        [this, source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String("controllers", 11);
            writeItems(w, /*restore=*/true);
        },
        [this, alive, source, cloud](bool ok, const rapidjson::Value& payload) {
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
            auto start = [this, source, cloud] {
                if (mRunning)
                    return;
                clearRunStream();
                mRunning = true;
                footer()->setStatus("Restoring controller config…");
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore",
                    [this, source](MadJson::Writer& w) {
                        w.Key("source");
                        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
                        w.Key("category");
                        w.String("controllers", 11);
                        writeItems(w, /*restore=*/true);
                    },
                    [this, a2, cloud](bool ok2, const rapidjson::Value& payload2) {
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

// ── stream ─────────────────────────────────────────────────────────────────────

void GuiMadPageControllers::attachRunStream(const std::string& token, bool restore, bool cloud)
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
                footer()->flash(msg + ".", 9000, false);
                if (orphaned > 0) {
                    const std::string snap {MadJson::getString(data, "snapshot")};
                    mWindow->pushGui(new MadMsgBox(
                        std::to_string(orphaned) +
                            " item(s) could not be fully restored and their previous copy was moved aside "
                            "for safety. Roll them back from RECOVERY.txt in:\n\n" + snap,
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
                    footer()->flash("Backed up controller config to MEGA.", 8000, false);
            }
            else {
                const int copied {MadJson::getInt(data, "copied", 0)};
                footer()->flash("Backed up " + std::to_string(copied) + " item(s).", 8000, false);
            }
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty())
            footer()->setStatus(line);
    });
}

void GuiMadPageControllers::clearRunStream()
{
    if (!mRunToken.empty()) {
        backend()->clearStreamCallback(mRunToken);
        mRunToken.clear();
    }
}

// ── input / focus ──────────────────────────────────────────────────────────────

bool GuiMadPageControllers::onBackPressed()
{
    if (mRunning)
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.", 4000,
                        false);
    return false;
}

bool GuiMadPageControllers::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("x", input)) {
        toggleCloud();
        return true;
    }
    if (input.value != 0 && config->isMappedTo("y", input)) {
        changeTarget();
        return true;
    }
    return mList != nullptr && mList->input(config, input);
}

void GuiMadPageControllers::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageControllers::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick / start"),
                                     HelpPrompt("x", mCloud ? "on this deck" : "use mega")};
    // Y has no real action in backup+cloud (MEGA has no folder to pick), so omit it there.
    if (!(mBackup && mCloud))
        prompts.emplace_back("y", mBackup ? "folder" : "change backup");
    prompts.emplace_back("b", "back");
    return prompts;
}

void GuiMadPageControllers::onSaveFocus()
{
    // nothing to persist: the group list keeps its own cursor across a stash (setRows keepCursor=true).
}

void GuiMadPageControllers::onRestoreFocus()
{
    // a source/location change may have altered the ticked groups: rebuild the rows on return.
    if (mList != nullptr)
        rebuildGroups();
}
