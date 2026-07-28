//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageChooseTarget.cpp
//
//  See GuiMadPageChooseTarget.h.
//

#include "guis/mad/pages/GuiMadPageChooseTarget.h"

#include "Window.h"
#include "guis/mad/GuiMadFolderPicker.h" // Local backup dest / the "Browse for a folder..." restore source
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h" // MadColor for the source-list rows
#include "guis/mad/widgets/MadTileGrid.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <algorithm> // std::sort (merge local + browsed sources newest-first)

namespace
{
    std::string titleFor(const std::string& mode)
    {
        return mode == "restore" ? "RESTORE FROM" : "BACK UP TO";
    }

    // A pickable-row id that can never collide with a real backup id (a backup id is an ABSOLUTE path
    // -> starts with '/'); the leading control char keeps it distinct. Marks the "Browse..." action row.
    constexpr const char* kBrowseSentinel {"\x01""browse"};
}

namespace MadChooser
{
    ChooserCfg esde() { return {"esde", "file", "esde.cloud_sources"}; }
    ChooserCfg emucfg() { return {"emucfg", "file", "emucfg.cloud_sources"}; }
}

GuiMadPageChooseTarget::GuiMadPageChooseTarget(GuiMadPanel* panel, const std::string& mode,
                                               const ChooserCfg& cfg, const OnResolved& onResolved)
    : MadPage {panel, titleFor(mode)}
    , mMode {mode}
    , mCfg {cfg}
    , mOnResolved {onResolved}
{
}

GuiMadPageChooseTarget::~GuiMadPageChooseTarget() = default;

void GuiMadPageChooseTarget::build()
{
    showTypeTiles();
}

// ── the Local / Cloud source-type tiles ──────────────────────────────────────

void GuiMadPageChooseTarget::hideTypeTiles()
{
    if (mTypeGrid != nullptr) {
        mTypeCookie = mTypeGrid->cursorIndex();
        removeChild(mTypeGrid.get());
        mTypeGrid.reset();
    }
}

void GuiMadPageChooseTarget::showTypeTiles()
{
    hideSourceList();
    mView = View::Types;
    setLoadingText("");
    mTypeGrid = std::make_shared<MadTileGrid>();
    mTypeGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mTypeGrid->setSize(mViewportSize.x, mViewportSize.y);
    std::vector<MadTileGrid::Tile> tiles;
    MadTileGrid::Tile local;
    local.key = "local";
    local.label = "Local";
    local.artPath = MadTheme::routerIconPath("backup-local");
    tiles.push_back(local);
    MadTileGrid::Tile cloud;
    cloud.key = "cloud";
    cloud.label = "Cloud (MEGA)";
    cloud.artPath = MadTheme::routerIconPath("backup-cloud-mega");
    tiles.push_back(cloud);
    mTypeGrid->setTiles(tiles);
    mTypeGrid->setCursorIndex(mTypeCookie);
    mTypeGrid->setOnPick([this](const std::string& k) { onPickType(k); });
    mTypeGrid->onFocusGained();
    addChild(mTypeGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageChooseTarget::onPickType(const std::string& kind)
{
    // A cloud connection check (Cloud pick) is a slow round-trip during which the tiles stay interactive.
    // Block a second pick until it resolves so the user can't start a COMPETING destination (e.g. pick
    // Cloud, then Local): its buffered reply would otherwise land after the Local pick. Same once a resolve
    // is already committed for this tick.
    if (mCheckingCloud || mHasPending)
        return;
    if (mMode == "restore") {
        mSourceKind = kind; // "local" | "cloud"
        showBackupList();
        return;
    }
    // backup: resolve the destination straight away.
    if (kind == "cloud")
        beginBackupCloud();
    else
        beginBackupLocal();
}

// ── backup: resolve a destination ────────────────────────────────────────────

void GuiMadPageChooseTarget::beginBackupLocal()
{
    // A Window-level modal (captures input, so the tiles underneath can't be navigated while it is up).
    // On a chosen folder, resolve the local destination.
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadFolderPicker(
        [this, alive](const std::string& path) {
            if (alive.expired() || path.empty()) // empty == cancelled
                return;
            MadTarget t;
            t.cloud = false;
            t.dest = path;
            resolveWith(t);
        },
        "PICK A BACKUP DESTINATION"));
}

void GuiMadPageChooseTarget::beginBackupCloud()
{
    if (mCheckingCloud)
        return;
    // Fail-fast: if MEGA isn't set up, don't push the drill page at all. cloud.status is a fast bounded
    // check; the drill page re-checks before it actually uploads (a connection can drop in between).
    mCheckingCloud = true;
    footer()->setStatus("Checking MEGA...");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "cloud.status", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mCheckingCloud = false;
            footer()->setStatus("");
            if (!ok || !MadJson::getBool(payload, "connected")) {
                footer()->flash("Not connected to MEGA. Run the cloud setup in Desktop Mode first.",
                                6000, true);
                return;
            }
            MadTarget t;
            t.cloud = true;
            resolveWith(t);
        },
        30000);
}

// ── restore: the chosen type's backup list ───────────────────────────────────

void GuiMadPageChooseTarget::showBackupList()
{
    hideTypeTiles();
    mView = View::List;
    setLoadingText("");
    ensureSourceList();
    rebuildSourceList();
    if (mSourceKind == "cloud") {
        if (!mCloudLoaded && !mCloudLoading)
            fetchCloudSources();
    }
    else if (!mLocalLoaded && !mLocalLoading) {
        fetchLocalSources();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageChooseTarget::ensureSourceList()
{
    if (mSourceList != nullptr)
        return;
    mSourceList = std::make_shared<MadVirtualList>();
    mSourceList->setPosition(mViewportPos.x, mViewportPos.y);
    mSourceList->setSize(mViewportSize.x, mViewportSize.y);
    mSourceList->setOnSelect([this](int i) { onPickSource(i); });
    addChild(mSourceList.get());
    mSourceList->onFocusGained();
}

void GuiMadPageChooseTarget::hideSourceList()
{
    if (mSourceList != nullptr) {
        removeChild(mSourceList.get());
        mSourceList.reset();
    }
}

void GuiMadPageChooseTarget::rebuildSourceList()
{
    if (mSourceList == nullptr)
        return;
    const bool cloud {mSourceKind == "cloud"};
    // LOCAL shows the remembered+default backups PLUS any found via the folder browser, de-duped by id
    // (a backup's absolute path) and newest-first. Kept in a local vector so a slow granular.sources
    // callback that clears mLocalSrc can never wipe the browsed entries.
    std::vector<Src> localCombined;
    if (!cloud) {
        localCombined = mLocalSrc;
        for (const Src& b : mBrowsedSrc) {
            bool dup {false};
            for (const Src& e : localCombined)
                if (e.id == b.id) { dup = true; break; }
            if (!dup)
                localCombined.push_back(b);
        }
        std::sort(localCombined.begin(), localCombined.end(),
                  [](const Src& a, const Src& c) { return a.created > c.created; });
    }
    // "loaded" gates the "loading..." note; browsed results count as loaded even if the default scan is
    // still in flight, so a browse result never hides behind "loading...".
    const bool loaded {cloud ? mCloudLoaded : (mLocalLoaded || !mBrowsedSrc.empty())};
    const std::vector<Src>& src {cloud ? mCloudSrc : localCombined};
    std::vector<MadVirtualList::Row> rows;
    mSourceRowId.clear();
    const unsigned int note {MadTheme::color(MadColor::Secondary)};
    const unsigned int item {MadTheme::color(MadColor::Primary)};
    const unsigned int action {MadTheme::color(MadColor::Title)};
    auto pushNote = [&](const std::string& t) {
        rows.push_back({t, note});
        mSourceRowId.push_back("");
    };
    // The Browse action is LOCAL-only and always available (independent of the backup scan).
    if (!cloud) {
        rows.push_back({"Browse for a folder...", action});
        mSourceRowId.push_back(kBrowseSentinel);
    }
    if (!loaded)
        pushNote(cloud ? "Looking on MEGA..." : "loading...");
    else if (cloud && !mCloudConnected)
        pushNote("(not connected - run the cloud setup in Desktop Mode)");
    else if (src.empty())
        pushNote(cloud ? "(none yet - back some up to MEGA first)"
                       : "(none yet - make a backup first, or Browse above)");
    else
        for (const Src& s : src) {
            rows.push_back({fmtSourceLabel(s.created, s.count), item});
            mSourceRowId.push_back(s.id);
        }

    const int prev {mSourceList->cursor()};
    mSourceList->setRows(rows, /*keepCursor=*/true);
    // Land the cursor on the first BACKUP row when the kept position isn't a real backup - including when
    // it sits on the always-present Browse action (row 0), so a fresh list with backups highlights the
    // newest one, not "Browse...". If there are NO backups, stay on Browse (don't force-move onto a note).
    const bool onBrowse {prev >= 0 && prev < static_cast<int>(mSourceRowId.size()) &&
                         mSourceRowId[prev] == kBrowseSentinel};
    if (prev < 0 || prev >= static_cast<int>(mSourceRowId.size()) || mSourceRowId[prev].empty() ||
        onBrowse) {
        int firstBackup {-1}, firstPickable {-1};
        for (int i = 0; i < static_cast<int>(mSourceRowId.size()); ++i) {
            if (mSourceRowId[i].empty())
                continue;
            if (firstPickable < 0)
                firstPickable = i;
            if (mSourceRowId[i] != kBrowseSentinel) { firstBackup = i; break; }
        }
        const int land {firstBackup >= 0 ? firstBackup : (onBrowse ? -1 : firstPickable)};
        if (land >= 0)
            mSourceList->setCursor(land);
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageChooseTarget::onPickSource(int index)
{
    if (index < 0 || index >= static_cast<int>(mSourceRowId.size()))
        return;
    const std::string id {mSourceRowId[index]};
    if (id.empty()) {
        footer()->flash("No backup to choose here yet.", 2500, false);
        return;
    }
    if (id == kBrowseSentinel) {
        openSourceBrowser();
        return;
    }
    MadTarget t;
    t.cloud = id.rfind("cloud:", 0) == 0;
    t.source = id;
    resolveWith(t);
}

void GuiMadPageChooseTarget::fetchLocalSources()
{
    mLocalLoading = true;
    const std::string category {mCfg.category};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.sources",
        [category](MadJson::Writer& w) {
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mLocalLoading = false;
            mLocalLoaded = true;
            mLocalSrc.clear();
            if (ok) {
                const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
                if (arr.IsArray())
                    for (const rapidjson::Value& s : arr.GetArray()) {
                        if (MadJson::getString(s, "kind") != "local")
                            continue;
                        mLocalSrc.push_back({MadJson::getString(s, "id"),
                                             MadJson::getString(s, "created"),
                                             MadJson::getInt(s, "count", 0)});
                    }
            }
            if (mView == View::List && mSourceKind != "cloud")
                rebuildSourceList();
        },
        10000);
}

void GuiMadPageChooseTarget::fetchCloudSources()
{
    mCloudLoading = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        mCfg.cloudSourcesRpc, nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            mCloudLoading = false;
            mCloudLoaded = true;
            mCloudConnected = ok && MadJson::getBool(payload, "connected");
            mCloudSrc.clear();
            if (ok) {
                const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
                if (arr.IsArray())
                    for (const rapidjson::Value& s : arr.GetArray())
                        mCloudSrc.push_back({MadJson::getString(s, "id"),
                                             MadJson::getString(s, "created"),
                                             MadJson::getInt(s, "count", 0)});
            }
            if (mView == View::List && mSourceKind == "cloud")
                rebuildSourceList();
        },
        // the list-* cloud sources cat each set's manifest over the network, so give it plenty of room.
        200000);
}

void GuiMadPageChooseTarget::openSourceBrowser()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadFolderPicker(
        [this, alive](const std::string& path) {
            if (alive.expired() || path.empty()) // empty == cancelled
                return;
            fetchLocalSourcesUnder(path);
        },
        "PICK A BACKUP FOLDER"));
}

void GuiMadPageChooseTarget::fetchLocalSourcesUnder(const std::string& path)
{
    footer()->flash("Looking for backups in that folder...", 2000, false);
    const std::string category {mCfg.category};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.sources_under",
        [path, category](MadJson::Writer& w) {
            w.Key("path");
            w.String(path.c_str(), static_cast<rapidjson::SizeType>(path.length()));
            w.Key("category");
            w.String(category.c_str(), static_cast<rapidjson::SizeType>(category.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                footer()->flash("Couldn't read that folder.", 3000, true);
                return;
            }
            int found {0}, added {0};
            const rapidjson::Value& arr {MadJson::getMember(payload, "sources")};
            if (arr.IsArray())
                for (const rapidjson::Value& s : arr.GetArray()) {
                    const std::string id {MadJson::getString(s, "id")};
                    if (id.empty())
                        continue;
                    ++found;
                    bool dup {false};
                    for (const Src& e : mBrowsedSrc)
                        if (e.id == id) { dup = true; break; }
                    if (!dup)
                        for (const Src& e : mLocalSrc)
                            if (e.id == id) { dup = true; break; }
                    if (dup)
                        continue;
                    mBrowsedSrc.push_back({id, MadJson::getString(s, "created"),
                                           MadJson::getInt(s, "count", 0)});
                    ++added;
                }
            if (found == 0)
                footer()->flash("No backups found in that folder.", 3000, false);
            else if (added == 0)
                footer()->flash("Those backups are already listed.", 2500, false);
            else
                footer()->flash(std::to_string(added) + " backup(s) added from that folder.", 2500,
                                false);
            if (mView == View::List && mSourceKind != "cloud")
                rebuildSourceList();
        },
        // an arbitrary folder scan is slow=True on the backend (may hold many entries).
        60000);
}

std::string GuiMadPageChooseTarget::fmtSourceLabel(const std::string& created, int count) const
{
    std::string when {created};
    if (created.size() == 15 && created[8] == 'T') // "YYYYmmddTHHMMSS" -> "YYYY-MM-DD HH:MM:SS"
        when = created.substr(0, 4) + "-" + created.substr(4, 2) + "-" + created.substr(6, 2) + " " +
               created.substr(9, 2) + ":" + created.substr(11, 2) + ":" + created.substr(13, 2);
    const std::string noun {count == 1 ? mCfg.countNoun : mCfg.countNoun + "s"};
    return when + "   -   " + std::to_string(count) + " " + noun;
}

// ── resolution ───────────────────────────────────────────────────────────────

void GuiMadPageChooseTarget::resolveWith(const MadTarget& t)
{
    // FIRST resolve wins. A destination can be chosen by an async callback (a cloud.status reply, a folder
    // picker), and while a Window modal (the folder picker) is up the panel's poll() is paused, so a cloud
    // status reply gets BUFFERED and would otherwise fire AFTER a later local-folder pick and overwrite it
    // (silently sending the backup to MEGA). Ignoring a second resolve keeps the destination the user
    // actually committed to first. Cleared in update() once fired, so a legit re-pick after backing out works.
    if (mHasPending)
        return;
    // Never push the drill page mid-input / mid-callback: stash it and fire onResolved on the next update()
    // (the chooser is still the current page at that point; the drill lands ON TOP, this stays beneath).
    mPending = t;
    mHasPending = true;
}

void GuiMadPageChooseTarget::update(int deltaTime)
{
    MadPage::update(deltaTime);
    if (mHasPending) {
        mHasPending = false;
        if (mOnResolved)
            mOnResolved(mPending);
    }
}

// ── input / focus ─────────────────────────────────────────────────────────────

bool GuiMadPageChooseTarget::onBackPressed()
{
    // restore drills Types -> List; B walks the List back up to the Types tiles instead of popping.
    if (mMode == "restore" && mView == View::List) {
        showTypeTiles();
        return true;
    }
    return false; // Types -> pop the page (back to the landing / restore hub)
}

bool GuiMadPageChooseTarget::input(InputConfig* config, Input input)
{
    if (mView == View::List)
        return mSourceList != nullptr && mSourceList->input(config, input);
    return mTypeGrid != nullptr && mTypeGrid->input(config, input);
}

void GuiMadPageChooseTarget::pageScroll(int direction)
{
    if (mView == View::List) {
        if (mSourceList != nullptr)
            mSourceList->pageScroll(direction);
    }
    else if (mTypeGrid != nullptr) {
        mTypeGrid->pageScroll(direction);
    }
}

std::vector<HelpPrompt> GuiMadPageChooseTarget::getHelpPrompts()
{
    if (mView == View::List)
        return mSourceList != nullptr ? mSourceList->getHelpPrompts() : std::vector<HelpPrompt>();
    return mTypeGrid != nullptr ? mTypeGrid->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageChooseTarget::onSaveFocus()
{
    if (mView == View::List) {
        if (mSourceList != nullptr)
            mSourceCookie = mSourceList->cursor();
    }
    else if (mTypeGrid != nullptr) {
        mTypeCookie = mTypeGrid->cursorIndex();
    }
    mHasPending = false; // a stashed/backgrounded page must not carry a stale deferred resolve
}

void GuiMadPageChooseTarget::onRestoreFocus()
{
    if (mView == View::List) {
        if (mSourceList != nullptr) {
            mSourceList->setCursor(mSourceCookie);
            mSourceList->onFocusGained();
        }
    }
    else if (mTypeGrid != nullptr) {
        mTypeGrid->setCursorIndex(mTypeCookie);
        mTypeGrid->onFocusGained();
    }
}
