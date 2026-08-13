//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupGroupListPage.cpp  (deck-patches)
//

#include "guis/mad/MadBackupGroupListPage.h"

#include "Window.h"
#include "components/TextComponent.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"

MadBackupGroupListPage::MadBackupGroupListPage(GuiMadPanel* panel, const std::string& title,
                                               const std::string& mode, const Params& params)
    : MadBackupFlowPage {panel, title, mode, params}
{
}

void MadBackupGroupListPage::build()
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

// ── the group list ───────────────────────────────────────────────────────────

void MadBackupGroupListPage::refetchForSource()
{
    fetchGroups();
}

void MadBackupGroupListPage::teardownContent()
{
    mGroups.clear();
    if (mList != nullptr)
        mList->setRows({}, /*keepCursor=*/false);
    onSourceReset();
}

void MadBackupGroupListPage::fetchGroups()
{
    setLoadingText("Loading " + mParams.noun + "...");
    const std::string source {mSource};
    const int gen {mSrcGen}; // a newer source pick (the bar) supersedes this fetch; drop a stale, out-of-
                             // order reply so it can't repopulate the group list under a different source
                             // (the groups RPC is slow=True, so replies can arrive reordered).
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
            onGroupsLoaded(mGroups);
            rebuildGroups();
        },
        20000);
}

void MadBackupGroupListPage::ensureWidgets()
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

std::string MadBackupGroupListPage::rowTail(const Group& g) const
{
    return "  (" + MadPageUtil::humanSizeCompact(g.size) + ")";
}

std::string MadBackupGroupListPage::rowText(const Group& g) const
{
    if (!g.present)
        return "○ " + g.label + "  (none)";
    return rowGlyph(g) + g.label + rowTail(g);
}

void MadBackupGroupListPage::rebuildGroups()
{
    ensureWidgets();
    mHeader->setText(headerText());
    const int extras {extraRowCount()};
    std::vector<MadVirtualList::Row> rows;
    rows.reserve(mGroups.size() + 1 + static_cast<size_t>(extras));
    for (const Group& g : mGroups) {
        const unsigned int col {!g.present ? MadTheme::color(MadColor::Secondary)
                                : groupTicked(g) ? MadTheme::color(MadColor::Primary)
                                                 : MadTheme::color(MadColor::Secondary)};
        rows.push_back({rowText(g), col});
    }
    // the action row: A here backs up / restores the ticked groups (X/Y drive the location bar).
    rows.push_back({std::string(mBackup ? "> Back up now" : "> Restore now"),
                    MadTheme::color(MadColor::Title)});
    for (int i {0}; i < extras; ++i)
        rows.push_back(extraRow(i));
    mList->setRows(rows, /*keepCursor=*/true);
    mPanel->refreshHelpPrompts();
    updateExplain();
}

void MadBackupGroupListPage::updateExplain()
{
    if (mExplain == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    const int n {static_cast<int>(mGroups.size())};
    if (c == n) { // the action row
        mExplain->setText(actionRowExplain());
        return;
    }
    if (c > n) { // an extra action row below it
        mExplain->setText(extraRowExplain(c - n - 1));
        return;
    }
    mExplain->setText(c >= 0 && c < n ? mGroups[c].explain : "");
}

std::string MadBackupGroupListPage::headerText() const
{
    int n {0};
    for (const Group& g : mGroups)
        if (groupTicked(g))
            ++n;
    // With extra rows below it the action row is no longer the last one, so point at it by name.
    const std::string hint {mBackup ? "last row backs up"
                            : extraRowCount() > 0 ? "the Restore now row restores"
                                                  : "last row restores"};
    return std::to_string(n) + " selected  ·  A tick  ·  " + hint;
}

void MadBackupGroupListPage::onListSelect(int listIndex)
{
    const int n {static_cast<int>(mGroups.size())};
    if (listIndex == n) { // the "Back up / Restore now" row
        act();
        return;
    }
    if (listIndex > n) { // an extra action row below it
        onExtraRow(listIndex - n - 1);
        return;
    }
    toggleAt(listIndex);
}

void MadBackupGroupListPage::toggleAt(int groupIndex)
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
    if (onGroupActivated(g)) // the page took it (a drill-down, say) instead of a plain tick
        return;
    g.selected = !g.selected;
    rebuildGroups();
}

bool MadBackupGroupListPage::anyTicked() const
{
    for (const Group& g : mGroups)
        if (groupTicked(g))
            return true;
    return false;
}

void MadBackupGroupListPage::act()
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

void MadBackupGroupListPage::writeGroupItems(const Group& g, bool restore, MadJson::Writer& w) const
{
    for (const File& f : g.files) {
        w.StartObject();
        if (restore) {
            w.Key("system");
            w.String(g.key.c_str(), static_cast<rapidjson::SizeType>(g.key.length()));
            w.Key("id");
        }
        else {
            w.Key("group");
            w.String(g.key.c_str(), static_cast<rapidjson::SizeType>(g.key.length()));
            w.Key("rel");
        }
        w.String(f.rel.c_str(), static_cast<rapidjson::SizeType>(f.rel.length()));
        w.EndObject();
    }
}

void MadBackupGroupListPage::writeItems(MadJson::Writer& w, bool restore) const
{
    w.Key("items");
    w.StartArray();
    for (const Group& g : mGroups)
        if (groupTicked(g))
            writeGroupItems(g, restore, w);
    w.EndArray();
}

// ── backup ───────────────────────────────────────────────────────────────────

void MadBackupGroupListPage::beginBackupLocal(const std::string& dest)
{
    if (mRunning)
        return;
    clearRunStream();
    mRunning = true;
    footer()->setStatus("Backing up " + mParams.noun + "...");
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        mParams.backupRpc,
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

void MadBackupGroupListPage::beginBackupCloud()
{
    if (mRunning)
        return;
    // Re-check the connection at the leaf: it may have dropped between the chooser's fail-fast check and now.
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Checking MEGA...");
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
            footer()->setStatus("Uploading " + mParams.noun + " to MEGA...");
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest(
                mParams.cloudPushRpc,
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

// ── restore ──────────────────────────────────────────────────────────────────

std::string MadBackupGroupListPage::actionRowExplain() const
{
    return mBackup
        ? "Press A to back up the ticked groups to the destination in the bar above."
        : "Press A to restore the ticked groups now (a recoverable copy of any replaced file is kept).";
}

std::string MadBackupGroupListPage::replaceWarningText(int replace) const
{
    return std::to_string(replace) + mParams.itemNoun +
           " already on disk will be REPLACED. A recoverable copy is saved aside first. Continue?";
}

void MadBackupGroupListPage::startRestore()
{
    if (mRunning || mRestorePreviewing)
        return;
    const std::string source {mSource};
    const bool cloud {source.rfind("cloud:", 0) == 0};
    // Supersede if the bar's source changes (X/Y) while the preview is in flight: the counts the user
    // is about to confirm were computed for THIS source, so confirming them against another one would
    // warn about the wrong files. The "All" restore has always guarded this; a single restore did not.
    const int gen {mSrcGen};
    mRestorePreviewing = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "granular.restore_preview",
        [this, source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
            w.Key("category");
            w.String(mParams.category.c_str(),
                     static_cast<rapidjson::SizeType>(mParams.category.length()));
            writeItems(w, /*restore=*/true);
        },
        [this, alive, gen, source, cloud](bool ok, const rapidjson::Value& payload) {
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
            auto start = [this, source, cloud] {
                if (mRunning)
                    return;
                clearRunStream();
                mRunning = true;
                footer()->setStatus(mParams.restoreStatus);
                std::weak_ptr<int> a2 {pageAlive()};
                pageRequest(
                    "granular.restore",
                    [this, source](MadJson::Writer& w) {
                        w.Key("source");
                        w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
                        w.Key("category");
                        w.String(mParams.category.c_str(),
                                 static_cast<rapidjson::SizeType>(mParams.category.length()));
                        writeItems(w, /*restore=*/true);
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
                mWindow->pushGui(new MadMsgBox(replaceWarningText(replace), "YES",
                                               [a3, start] { if (!a3.expired()) start(); }, "CANCEL",
                                               nullptr));
            }
            else {
                start();
            }
        },
        20000);
}

// ── input / focus ────────────────────────────────────────────────────────────

bool MadBackupGroupListPage::onBackPressed()
{
    if (mRunning)
        footer()->flash(std::string(mBackup ? "Backing up" : "Restoring") + " in the background.", 4000,
                        false);
    return false;
}

bool MadBackupGroupListPage::input(InputConfig* config, Input input)
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

void MadBackupGroupListPage::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->pageScroll(direction);
}

std::vector<HelpPrompt> MadBackupGroupListPage::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down", "choose"), HelpPrompt("a", "tick / start"),
                                     HelpPrompt("x", mCloud ? "on this deck" : "use mega")};
    // Y has no real action in backup+cloud (MEGA has no folder to pick), so omit it there.
    if (!(mBackup && mCloud))
        prompts.emplace_back("y", mBackup ? "folder" : "change backup");
    prompts.emplace_back("b", "back");
    return prompts;
}

void MadBackupGroupListPage::onSaveFocus()
{
    // nothing to persist: the group list keeps its own cursor across a stash (setRows keepCursor=true).
}

void MadBackupGroupListPage::onRestoreFocus()
{
    // a source/location change may have altered the ticked groups: rebuild the rows on return.
    if (mList != nullptr)
        rebuildGroups();
}
