//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsde.cpp  (deck-patches, P6)
//

#include "guis/mad/pages/GuiMadPageEsde.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadFolderPicker.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (the restore "change backup" list)
#include "guis/mad/pages/GuiMadPageEsdeGamelists.h"
#include "guis/mad/widgets/MadVirtualList.h"
#include "utils/PlatformUtil.h"

#include <cstdlib>
#include <tuple>

namespace
{
    std::string humanBytes(long long n)
    {
        double d {static_cast<double>(n < 0 ? 0 : n)};
        const char* u[] {"B", "K", "M", "G"};
        int i {0};
        while (d >= 1024.0 && i < 3) {
            d /= 1024.0;
            ++i;
        }
        char buf[32];
        std::snprintf(buf, sizeof(buf), (i == 0 ? "%.0f%s" : "%.1f%s"), d, u[i]);
        return buf;
    }
}

GuiMadPageEsde::GuiMadPageEsde(GuiMadPanel* panel, const std::string& mode)
    : MadPage {panel, mode == "restore" ? "RESTORE ES-DE SETTINGS" : "BACK UP ES-DE SETTINGS"}
    , mMode {mode}
    , mSource {mode == "restore" ? "" : "live"}
    , mBackup {mode != "restore"}
{
}

GuiMadPageEsde::~GuiMadPageEsde()
{
    clearRunStream();
}

void GuiMadPageEsde::build()
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
    }
    else {
        resolveDefaultSource();
    }
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

void GuiMadPageEsde::ensureBar()
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

std::string GuiMadPageEsde::barText() const
{
    if (mBackup) {
        if (mCloud)
            return "Save to:  MEGA cloud       ( X: use On this Deck )";
        const std::string dest {mDest.empty() ? "(loading...)" : shortPath(mDest)};
        return "Save to:  On this Deck  " + dest + "       ( Y: change folder    X: use MEGA )";
    }
    const std::string label {mHasSource ? fmtWhen(mSrcCreated) + "  (" + std::to_string(mSrcCount) +
                                              (mSrcCount == 1 ? " file)" : " files)")
                                        : "(no backup found)"};
    if (mCloud)
        return "Restore from:  MEGA  " + label + "       ( Y: change    X: use On this Deck )";
    return "Restore from:  On this Deck  " + label + "       ( Y: change    X: use MEGA )";
}

void GuiMadPageEsde::refreshBar()
{
    if (mBar != nullptr)
        mBar->setText(barText());
}

void GuiMadPageEsde::toggleCloud()
{
    if (mChecking)
        return;
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
            },
            30000);
        return;
    }
    mCloud = false;
    if (!mBackup)
        resolveDefaultSource();
    refreshBar();
}

void GuiMadPageEsde::changeTarget()
{
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

void GuiMadPageEsde::resolveDefaultSource()
{
    mHasSource = false;
    mSource.clear();
    mGroups.clear();
    mGamelistRels.clear();
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
            setLoadingText(cloud ? "No ES-DE settings backup on MEGA yet."
                                 : "No local backup found. Press Y to browse.");
            refreshBar();
            return;
        }
        applySource(id, created, count);
    };
    if (mCloud) {
        pageRequest("esde.cloud_sources", nullptr,
                    [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, true); }, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [](MadJson::Writer& w) {
                w.Key("category");
                w.String("esde", 4);
            },
            [handle](bool ok, const rapidjson::Value& p) { handle(ok, p, false); }, 10000);
    }
    refreshBar();
}

void GuiMadPageEsde::applySource(const std::string& id, const std::string& created, int count)
{
    ++mSrcGen;
    mSource = id;
    mSrcCreated = created;
    mSrcCount = count;
    mHasSource = true;
    refreshBar();
    fetchGroups();
}

void GuiMadPageEsde::openSourcePicker()
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
            footer()->flash("No ES-DE settings backup on MEGA yet.", 3500, false);
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
        pageRequest("esde.cloud_sources", nullptr, present, 200000);
    }
    else {
        pageRequest(
            "granular.sources",
            [](MadJson::Writer& w) {
                w.Key("category");
                w.String("esde", 4);
            },
            present, 10000);
    }
}

void GuiMadPageEsde::browseForSource()
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
                    w.String("esde", 4);
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
                        footer()->flash("No ES-DE settings backup found in that folder.", 3500, false);
                        return;
                    }
                    mCloud = false;
                    applySource(id, created, count);
                },
                60000);
        },
        "PICK A BACKUP FOLDER"));
}

bool GuiMadPageEsde::groupTicked(const Group& g) const
{
    if (isGamelists(g)) {
        for (const File& f : g.files)
            if (mGamelistRels.count(f.rel))
                return true;
        return false;
    }
    return g.selected;
}

// ── the group list ───────────────────────────────────────────────────────────

void GuiMadPageEsde::fetchGroups()
{
    setLoadingText("Loading ES-DE settings…");
    const std::string source {mSource};
    const int gen {mSrcGen}; // a newer source pick (the bar) supersedes this fetch; drop a stale, out-of-
                             // order esde.groups reply so it can't repopulate the group list under a
                             // different source (esde.groups is slow=True, so replies can arrive reordered).
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "esde.groups",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive, gen](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || gen != mSrcGen)
                return;
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load ES-DE settings: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mGroups.clear();
            mGamelistRels.clear();
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
                    g.selected = g.present && !isGamelists(g);
                    if (g.present && isGamelists(g))
                        for (const File& f : g.files)
                            mGamelistRels.insert(f.rel);
                    if (!g.key.empty())
                        mGroups.push_back(g);
                }
            rebuildGroups();
        },
        20000);
}

void GuiMadPageEsde::ensureWidgets()
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

std::string GuiMadPageEsde::rowText(const Group& g) const
{
    std::string glyph;
    std::string tail {"  (" + humanBytes(g.size) + ")"};
    if (isGamelists(g)) {
        int ticked {0};
        for (const File& f : g.files)
            if (mGamelistRels.count(f.rel))
                ++ticked;
        const int total {static_cast<int>(g.files.size())};
        glyph = ticked == 0 ? "○ " : ticked == total ? "● " : "◐ ";
        tail = "  (" + std::to_string(ticked) + " of " + std::to_string(total) + " systems)";
    }
    else {
        glyph = g.selected ? "● " : "○ ";
    }
    if (!g.present)
        return "○ " + g.label + "  (none)";
    return glyph + g.label + tail;
}

void GuiMadPageEsde::rebuildGroups()
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
    // the LAST row is the action: A here backs up / restores the ticked groups (X/Y drive the location bar).
    rows.push_back({std::string(mBackup ? "> Back up now" : "> Restore now"),
                    MadTheme::color(MadColor::Title)});
    mList->setRows(rows, /*keepCursor=*/true);
    mPanel->refreshHelpPrompts();
    updateExplain();
}

void GuiMadPageEsde::updateExplain()
{
    if (mExplain == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c >= static_cast<int>(mGroups.size())) { // the action row
        mExplain->setText(mBackup
            ? "Press A to back up the ticked groups to the destination in the bar above."
            : "Press A to restore the ticked groups. ES-DE settings apply the next time ES-DE starts.");
        return;
    }
    mExplain->setText(c >= 0 && c < static_cast<int>(mGroups.size()) ? mGroups[c].explain : "");
}

std::string GuiMadPageEsde::headerText() const
{
    int n {0};
    for (const Group& g : mGroups)
        if (groupTicked(g))
            ++n;
    return std::to_string(n) + " selected  ·  A tick  ·  " +
           (mBackup ? "last row backs up" : "last row restores");
}

void GuiMadPageEsde::onListSelect(int listIndex)
{
    if (listIndex >= static_cast<int>(mGroups.size())) { // the last "Back up / Restore now" row
        act();
        return;
    }
    toggleAt(listIndex);
}

void GuiMadPageEsde::toggleAt(int groupIndex)
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
    if (isGamelists(g)) { // the gamelists group drills to pick individual systems (A opens it)
        openGamelistDrill();
        return;
    }
    g.selected = !g.selected;
    rebuildGroups();
}

void GuiMadPageEsde::openGamelistDrill()
{
    if (mRunning)
        return;
    for (const Group& g : mGroups)
        if (isGamelists(g)) {
            if (!g.present) {
                footer()->flash("No game favorites or metadata here.", 2500, false);
                return;
            }
            mPanel->pushPage(new GuiMadPageEsdeGamelists(mPanel, this, mSource));
            return;
        }
}

bool GuiMadPageEsde::anyTicked() const
{
    if (!mGamelistRels.empty())
        return true;
    for (const Group& g : mGroups)
        if (!isGamelists(g) && g.selected)
            return true;
    return false;
}

void GuiMadPageEsde::act()
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

void GuiMadPageEsde::writeItems(MadJson::Writer& w, bool restore) const
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
    for (const Group& g : mGroups) {
        if (isGamelists(g)) {
            for (const File& f : g.files)
                if (mGamelistRels.count(f.rel))
                    one(g.key, f.rel);
        }
        else if (g.selected) {
            for (const File& f : g.files)
                one(g.key, f.rel);
        }
    }
    w.EndArray();
}

// ── backup ───────────────────────────────────────────────────────────────────

void GuiMadPageEsde::beginBackupLocal(const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true;
    footer()->setStatus("Backing up ES-DE settings…");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.backup_esde",
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

void GuiMadPageEsde::beginBackupCloud()
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
            footer()->setStatus("Uploading ES-DE settings to MEGA…");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                "cloud.push_esde",
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

// ── staged restore ─────────────────────────────────────────────────────────────

void GuiMadPageEsde::startRestore()
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
            w.String("esde", 4);
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
                footer()->setStatus("Staging ES-DE settings…");
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore",
                    [this, source](MadJson::Writer& w) {
                        w.Key("source");
                        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
                        w.Key("category");
                        w.String("esde", 4);
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
                    std::to_string(replace) + " ES-DE setting(s) on disk will be REPLACED when ES-DE "
                    "restarts. A recoverable copy is saved aside first. Continue?",
                    "YES", [a3, start] { if (!a3.expired()) start(); }, "CANCEL", nullptr));
            }
            else {
                start();
            }
        },
        20000);
}

void GuiMadPageEsde::offerRestart()
{
    // Staged config applies on the NEXT start; offer a one-tap restart (wrapper-aware, like the precious
    // restore). RESTART re-execs $MAD_WRAPPER in place; without it, a QUIT applies on the next manual launch.
    const bool madRestart {std::getenv("MAD_WRAPPER") != nullptr};
    mWindow->pushGui(new MadMsgBox(
        "Your ES-DE settings are staged and apply the next time ES-DE starts.\n\nRestart ES-DE now to "
        "apply them?",
        madRestart ? "RESTART ES-DE" : "QUIT ES-DE",
        [madRestart] {
            Utils::Platform::quitES(madRestart ? Utils::Platform::QuitMode::RESTART
                                               : Utils::Platform::QuitMode::QUIT);
        },
        "LATER", [] {}));
}

// ── stream ─────────────────────────────────────────────────────────────────────

void GuiMadPageEsde::attachRunStream(const std::string& token, bool restore, bool cloud)
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
                footer()->flash("Staged " + std::to_string(restored) +
                                    " ES-DE setting(s) for the next start.",
                                8000, false);
                if (MadJson::getBool(data, "deferred"))
                    offerRestart();
            }
            else if (cloud) {
                footer()->flash("Backed up ES-DE settings to MEGA.", 8000, false);
            }
            else {
                const int copied {MadJson::getInt(data, "copied", 0)};
                footer()->flash("Backed up " + std::to_string(copied) + " ES-DE setting(s).", 8000, false);
            }
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty())
            footer()->setStatus(line);
    });
}

void GuiMadPageEsde::clearRunStream()
{
    if (!mRunToken.empty()) {
        backend()->clearStreamCallback(mRunToken);
        mRunToken.clear();
    }
}

// ── input / focus ──────────────────────────────────────────────────────────────

bool GuiMadPageEsde::onBackPressed()
{
    if (mRunning)
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.", 4000,
                        false);
    return false;
}

bool GuiMadPageEsde::input(InputConfig* config, Input input)
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

void GuiMadPageEsde::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageEsde::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick / start"),
                                     HelpPrompt("x", mCloud ? "on this deck" : "use mega"),
                                     HelpPrompt("y", mBackup ? "folder" : "change backup"),
                                     HelpPrompt("b", "back")};
    return prompts;
}

void GuiMadPageEsde::onSaveFocus()
{
    // nothing to persist: the group list keeps its own cursor across a stash (setRows keepCursor=true).
}

void GuiMadPageEsde::onRestoreFocus()
{
    // returning from the gamelist drill: refresh the group row (its ticked-systems count may have changed).
    if (mList != nullptr)
        rebuildGroups();
}
