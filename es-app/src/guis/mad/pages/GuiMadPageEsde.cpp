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
#include "guis/mad/pages/GuiMadPageEsdeGamelists.h"
#include "guis/mad/widgets/MadVirtualList.h"
#include "utils/PlatformUtil.h"

#include <cstdlib>

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

GuiMadPageEsde::GuiMadPageEsde(GuiMadPanel* panel, const std::string& mode, const std::string& source)
    : MadPage {panel, mode == "restore" ? "RESTORE ES-DE SETTINGS" : "BACK UP ES-DE SETTINGS"}
    , mMode {mode}
    , mSource {source}
    , mBackup {mode != "restore"}
{
}

GuiMadPageEsde::~GuiMadPageEsde()
{
    clearRunStream();
}

void GuiMadPageEsde::build()
{
    // Restore from MEGA with no set chosen yet ("cloud" sentinel) -> pick a cloud set first.
    if (mMode == "restore" && mSource == "cloud")
        showCloudSourceList();
    else
        fetchGroups();
}

void GuiMadPageEsde::update(int deltaTime)
{
    MadPage::update(deltaTime);
    if (mPending == Pending::ShowGroups) {
        mPending = Pending::None;
        hideSourceList();
        fetchGroups();
    }
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
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "esde.groups",
        [source](MadJson::Writer& w) {
            w.Key("source");
            w.String(source.c_str(), static_cast<rapidjson::SizeType>(source.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
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

    mHeader = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_SMALL),
                                              MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                              ALIGN_CENTER, glm::ivec2 {0, 1});
    mHeader->setPosition(mViewportPos.x, mViewportPos.y);
    mHeader->setSize(listWidth, 0.0f);
    addChild(mHeader.get());

    const float listTop {mViewportPos.y + headerHeight};
    mList = std::make_shared<MadVirtualList>();
    mList->setPosition(mViewportPos.x, listTop);
    mList->setSize(listWidth, mViewportPos.y + mViewportSize.y - listTop);
    mList->setOnSelect([this](int i) { toggleAt(i); });
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
    rows.reserve(mGroups.size());
    for (const Group& g : mGroups) {
        const unsigned int col {!g.present ? MadTheme::color(MadColor::Secondary)
                                : groupTicked(g) ? MadTheme::color(MadColor::Primary)
                                                 : MadTheme::color(MadColor::Secondary)};
        rows.push_back({rowText(g), col});
    }
    mList->setRows(rows, /*keepCursor=*/true);
    mPanel->refreshHelpPrompts();
    updateExplain();
}

void GuiMadPageEsde::updateExplain()
{
    if (mExplain == nullptr)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    mExplain->setText(c >= 0 && c < static_cast<int>(mGroups.size()) ? mGroups[c].explain : "");
}

std::string GuiMadPageEsde::headerText() const
{
    int n {0};
    for (const Group& g : mGroups)
        if (groupTicked(g))
            ++n;
    const std::string act {mBackup ? "X back up" : "X restore"};
    return std::to_string(n) + " selected · A tick · Y systems · " + act;
}

void GuiMadPageEsde::toggleAt(int i)
{
    if (mRunning) {
        footer()->flash("A backup or restore is already running - let it finish first.", 4000, true);
        return;
    }
    if (i < 0 || i >= static_cast<int>(mGroups.size()))
        return;
    Group& g {mGroups[i]};
    if (!g.present) {
        footer()->flash("Nothing to back up in that group.", 2500, false);
        return;
    }
    if (isGamelists(g)) {
        bool any {false};
        for (const File& f : g.files)
            if (mGamelistRels.count(f.rel)) { any = true; break; }
        if (any)
            for (const File& f : g.files)
                mGamelistRels.erase(f.rel);
        else
            for (const File& f : g.files)
                mGamelistRels.insert(f.rel);
    }
    else {
        g.selected = !g.selected;
    }
    rebuildGroups();
}

void GuiMadPageEsde::openGamelistDrill()
{
    if (mRunning)
        return;
    const int c {mList != nullptr ? mList->cursor() : -1};
    if (c < 0 || c >= static_cast<int>(mGroups.size()) || !isGamelists(mGroups[c])) {
        footer()->flash("Highlight 'Game favorites & metadata', then press Y to pick systems.", 3500, false);
        return;
    }
    if (!mGroups[c].present) {
        footer()->flash("No game metadata to restore here.", 2500, false);
        return;
    }
    mPanel->pushPage(new GuiMadPageEsdeGamelists(mPanel, this, mSource));
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
    if (mBackup)
        startBackupChooser();
    else
        startRestore();
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

void GuiMadPageEsde::startBackupChooser()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "Where should this ES-DE settings backup go?",
        "ON THIS DECK",
        [this, alive] {
            if (alive.expired())
                return;
            mWindow->pushGui(new GuiMadFolderPicker(
                [this, alive](const std::string& dest) {
                    if (alive.expired() || dest.empty())
                        return;
                    beginBackupLocal(dest);
                },
                "PICK A BACKUP DESTINATION"));
        },
        "MEGA CLOUD",
        [this, alive] {
            if (!alive.expired())
                beginBackupCloud();
        }));
}

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

// ── restore-mode cloud source list (clone of GuiMadPageBios) ────────────────────

void GuiMadPageEsde::showCloudSourceList()
{
    mPickingSource = true;
    ensureSourceList();
    rebuildSourceList();
    if (!mCloudLoaded && !mCloudLoading)
        fetchCloudSources();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageEsde::ensureSourceList()
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

void GuiMadPageEsde::hideSourceList()
{
    mPickingSource = false;
    if (mSourceList != nullptr) {
        mSourceCookie = mSourceList->cursor();
        removeChild(mSourceList.get());
        mSourceList.reset();
    }
}

void GuiMadPageEsde::rebuildSourceList()
{
    if (mSourceList == nullptr)
        return;
    std::vector<MadVirtualList::Row> rows;
    mSourceRowId.clear();
    const unsigned int note {MadTheme::color(MadColor::Secondary)};
    const unsigned int item {MadTheme::color(MadColor::Primary)};
    auto pushNote = [&](const std::string& t) {
        rows.push_back({t, note});
        mSourceRowId.push_back("");
    };
    if (!mCloudLoaded)
        pushNote("Looking on MEGA...");
    else if (!mCloudConnected)
        pushNote("(not connected - run the cloud setup in Desktop Mode)");
    else if (mCloudSrc.empty())
        pushNote("(none yet - back some ES-DE settings up to MEGA first)");
    else
        for (const Src& s : mCloudSrc) {
            rows.push_back({fmtSourceLabel(s.created, s.count), item});
            mSourceRowId.push_back(s.id);
        }
    mSourceList->setRows(rows, /*keepCursor=*/true);
    const int prev {mSourceList->cursor()};
    if (prev < 0 || prev >= static_cast<int>(mSourceRowId.size()) || mSourceRowId[prev].empty()) {
        for (int i = 0; i < static_cast<int>(mSourceRowId.size()); ++i)
            if (!mSourceRowId[i].empty()) { mSourceList->setCursor(i); break; }
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageEsde::fetchCloudSources()
{
    mCloudLoading = true;
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "esde.cloud_sources", nullptr,
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
            if (mPickingSource)
                rebuildSourceList();
        },
        200000);
}

void GuiMadPageEsde::onPickSource(int index)
{
    if (index < 0 || index >= static_cast<int>(mSourceRowId.size()))
        return;
    const std::string id {mSourceRowId[index]};
    if (id.empty()) {
        footer()->flash("No backup to choose here yet.", 2500, false);
        return;
    }
    mSource = id;
    mPending = Pending::ShowGroups;
}

std::string GuiMadPageEsde::fmtSourceLabel(const std::string& created, int count)
{
    std::string when {created};
    if (created.size() == 15 && created[8] == 'T')
        when = created.substr(0, 4) + "-" + created.substr(4, 2) + "-" + created.substr(6, 2) + " " +
               created.substr(9, 2) + ":" + created.substr(11, 2) + ":" + created.substr(13, 2);
    return when + "   -   " + std::to_string(count) + (count == 1 ? " file" : " files");
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
    if (mSourceList != nullptr)
        return mSourceList->input(config, input);
    if (input.value != 0 && config->isMappedTo("y", input) && mList != nullptr) {
        openGamelistDrill();
        return true;
    }
    if (input.value != 0 && config->isMappedTo("x", input)) {
        act();
        return true;
    }
    return mList != nullptr && mList->input(config, input);
}

void GuiMadPageEsde::pageScroll(int direction)
{
    if (mSourceList != nullptr)
        mSourceList->pageScroll(direction);
    else if (mList != nullptr)
        mList->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageEsde::getHelpPrompts()
{
    if (mSourceList != nullptr)
        return mSourceList->getHelpPrompts();
    return mList != nullptr ? mList->getHelpPrompts() : std::vector<HelpPrompt>();
}

void GuiMadPageEsde::onSaveFocus()
{
    if (mSourceList != nullptr)
        mSourceCookie = mSourceList->cursor();
}

void GuiMadPageEsde::onRestoreFocus()
{
    if (mSourceList != nullptr) {
        mSourceList->setCursor(mSourceCookie);
        return;
    }
    // returning from the gamelist drill: refresh the group row (its ticked-systems count may have changed).
    if (mList != nullptr)
        rebuildGroups();
}
