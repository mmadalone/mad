//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackup.cpp
//
//  MAD control panel: Backup / Restore (deck-patches).
//

#include "guis/mad/pages/GuiMadPageBackup.h"

#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/GuiMadFolderPicker.h" // the CHANGE DESTINATION browser
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (server picker)
#include "guis/mad/pages/GuiMadPageBackupRestore.h" // the granular per-game backup/restore hub
#include "guis/mad/pages/GuiMadPageBios.h"          // the per-system BIOS backup/restore
#include "guis/mad/pages/GuiMadPageChooseTarget.h"  // the standard dest/source chooser (first step)
#include "guis/mad/pages/GuiMadPageCloudProgress.h" // CloudProgress + the progress subpage
#include "guis/mad/pages/GuiMadPageEmu.h"           // Emulator config+data backup (per-emulator)
#include "guis/mad/pages/GuiMadPageEsde.h"          // ES-DE settings backup (grouped)
#include "guis/mad/pages/GuiMadPageSystem.h"        // System config backup (grouped, live restore)
#include "guis/mad/pages/GuiMadPageControllers.h"   // Controller config backup (grouped, live restore)
#include "guis/mad/pages/GuiMadPageManage.h"        // the Manage backups category hub (delete sets)
#include "guis/mad/pages/GuiMadPageRestoreHub.h"    // the Restore category hub (Game / Settings / BIOS)
#include "guis/mad/widgets/MadTileGrid.h"           // the Landing tile grid
#include "utils/PlatformUtil.h"                      // quitES(QuitMode::RESTART) for the restore prompt

#include <cstdio>
#include <cstdlib>
#include "guis/mad/MadTheme.h"

namespace
{
    // The deck-backup.sh categories, in the Tk page's order, grouped into the
    // chip rows. Keys = the script's --sizes keys AND the include-map keys.
    struct Category {
        const char* key;
        const char* label;
        bool defaultOn;
    };
    const std::vector<std::vector<Category>> CATEGORY_ROWS {
        {{"esde", "ES-DE", true},
         {"emu", "Emulator config + data", true},
         {"saves", "Saves", true},
         {"bios", "BIOS", true}},
        {{"cores", "RetroArch cores", true},
         {"bezels", "Bezels", false},
         {"rpcs3games", "RPCS3 installed games", false},
         {"pcsx2tex", "PCSX2 HD textures", false}},
        {{"ryujinxgames", "Ryujinx games", false},
         {"media", "Downloaded media", false}},
        // ROMs (SD + internal + OpenBOR) are no longer an all-or-nothing toggle here - they are chosen
        // per-game via the "Choose games" picker below, so a backup includes exactly the games you pick.
    };
} // namespace

GuiMadPageBackup::GuiMadPageBackup(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "BACKUP / RESTORE"}
    , mSizesDone {false}
    , mRunning {false}
{
    // Root (Landing): owns the durable include toggles + the shared transfer progress.
    for (const auto& row : CATEGORY_ROWS) {
        for (const Category& category : row)
            mInclude[category.key] = category.defaultOn;
    }
    mCloudProgress = std::make_shared<CloudProgress>();
}

GuiMadPageBackup::GuiMadPageBackup(GuiMadPanel* panel, GuiMadPageBackup* root, Section section)
    : MadLightgunPageBase {panel, section == Section::Cloud ? "FULL BACKUP" : "LOCAL BACKUP"}
    , mSection {section}
    , mRoot {root}
    , mSizesDone {false}
    , mRunning {false}
{
    // Transient subpage: the durable include map + mCloudProgress live on mRoot; this instance only
    // holds its own DISPLAY state (sizes, cloud status/servers/categories, chip rows).
}

GuiMadPageBackup::~GuiMadPageBackup()
{
    // Only the durable Landing (mRoot) owns the streams: a transient Local/Cloud subpage must NOT
    // clear them — mRunToken belongs to the root's live transfer, and clearing it here would detach
    // a running job. A subpage's own backup.sizes callback is already inert once its page-alive
    // token expires, so leaving it registered is harmless (it self-guards before touching `this`).
    if (this != mRoot)
        return;
    // Detach only — the sizes stream finishes and fills the daemon-side cache, and a running
    // transfer keeps going (leaving must not kill it; closing the whole panel does).
    if (!mSizesToken.empty())
        backend()->clearStreamCallback(mSizesToken);
    if (!mRunToken.empty())
        backend()->clearStreamCallback(mRunToken);
}

std::string GuiMadPageBackup::human(const long long bytes)
{
    double n {static_cast<double>(bytes)};
    for (const char* unit : {"B", "K", "M", "G", "T"}) {
        if (n < 1024.0 || unit[0] == 'T') {
            char buf[32];
            if (unit[0] == 'B' || unit[0] == 'K')
                std::snprintf(buf, sizeof(buf), "%.0f%s", n, unit);
            else
                std::snprintf(buf, sizeof(buf), "%.1f%s", n, unit);
            return buf;
        }
        n /= 1024.0;
    }
    return "";
}

std::string GuiMadPageBackup::chipLabel(const std::string& key) const
{
    std::string label;
    for (const auto& row : CATEGORY_ROWS) {
        for (const Category& category : row) {
            if (key == category.key)
                label = category.label;
        }
    }
    const auto it = mSizes.find(key);
    if (it != mSizes.end())
        label += " · " + human(it->second);
    return label;
}

std::string GuiMadPageBackup::cloudCatLabel(const std::string& key, const std::string& label) const
{
    // Tier A has a cloud-specific POST-FILTER size (cloud.sizes) = what the cloud actually
    // uploads, smaller than the local full-backup size. Prefer it; else fall back to mSizes
    // (Tier B syncs wholesale, so its full size IS its upload size), else no size yet.
    const auto cit = mCloudSizes.find(key);
    if (cit != mCloudSizes.end())
        return label + " · " + human(cit->second);
    const auto it = mSizes.find(key);
    return it != mSizes.end() ? label + " · " + human(it->second) : label;
}

void GuiMadPageBackup::updateCloudTally()
{
    // Sum the ON categories, and decide "(calculating…)" PER SELECTION: it shows only while a
    // SELECTED category's shown size is still provisional (a source that could still change it is
    // streaming). Nothing selected (or every selected size already final) -> no "(calculating…)".
    // Tier A prefers the cloud POST-FILTER size (mCloudSizes), falling back to the raw size
    // (mSizes) until cloud.sizes lands; Tier B totals the raw sizes (it uploads wholesale).
    auto tierTally = [this](const std::vector<std::pair<std::string, std::string>>& cats,
                            const bool preferCloud) {
        long long total {0};
        bool calculating {false};
        for (const auto& c : cats) {
            const bool on {mCatOn.count(c.first) ? mCatOn.at(c.first) : true};
            if (!on)
                continue;
            const auto cit {mCloudSizes.find(c.first)};
            const auto sit {mSizes.find(c.first)};
            if (preferCloud && cit != mCloudSizes.end()) {
                total += cit->second; // final: the cloud post-filter size arrived
            }
            else if (sit != mSizes.end()) {
                total += sit->second; // Tier B final; Tier A a fallback still shrinking to the cloud
                if (preferCloud && !mCloudSizesDone)
                    calculating = true;
            }
            else if (preferCloud ? (!mCloudSizesDone || !mSizesDone) : !mSizesDone) {
                calculating = true; // no size for this ON category yet, a source may still deliver
            }
        }
        return std::pair<long long, bool> {total, calculating};
    };
    if (mCloudTallyA != nullptr) {
        const auto ta {tierTally(mCatA, true)};
        mCloudTallyA->setText("  Selected: " + human(ta.first) +
                              (ta.second ? "   (calculating…)" : ""));
    }
    if (mCloudTallyB != nullptr) {
        const auto tb {tierTally(mCatB, false)};
        mCloudTallyB->setText("  Selected: " + human(tb.first) +
                              (tb.second ? "   (calculating…)" : ""));
    }
}

void GuiMadPageBackup::build()
{
    rebuild();

    if (mSection == Section::Landing) {
        // Reattach to any transfer already running (incl. a daemon auto-resume) and, if a restore
        // was interrupted last session, offer to resume it.
        fetchActive();
        return;
    }

    // Per-category sizes stream in as deck-backup.sh --sizes computes them (du over big trees — the
    // daemon caches them for this panel session). Both subpages want them: Local for the full-backup
    // chips, Cloud for the Tier-B "syncs wholesale" sizes + tally.
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest("backup.sizes", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (!ok)
                        return; // Sizes are decoration; the page works without.
                    // The daemon's cache snapshot: a single-flight stream may
                    // already have pushed keys before we subscribed.
                    const rapidjson::Value& sizes {MadJson::getMember(payload, "sizes")};
                    if (sizes.IsObject()) {
                        for (auto it = sizes.MemberBegin(); it != sizes.MemberEnd();
                             ++it) {
                            if (it->value.IsInt64())
                                mSizes[it->name.GetString()] = it->value.GetInt64();
                        }
                        if (!mSizes.empty())
                            deferRelayout([this] { rebuild(); });
                    }
                    const std::string token {MadJson::getString(payload, "stream")};
                    if (token.empty())
                        return;
                    mSizesToken = token;
                    backend()->setStreamCallback(
                        token, [this, alive](const rapidjson::Value& data) {
                            if (alive.expired())
                                return;
                            onSizePush(data);
                        });
                });

    // Full backup (Section::Cloud): the cloud connection/toggle state + server list (fetchCloud), PLUS a
    // cloud.status probe that defaults the destination toggle to MEGA when connected (cloud-default). Until it
    // returns the bar shows "(checking...)"; then the page shows ONLY the chosen destination's controls.
    if (mSection == Section::Cloud) {
        fetchCloud();
        std::weak_ptr<int> aFull {pageAlive()};
        pageRequest("cloud.status", nullptr, [this, aFull](bool ok, const rapidjson::Value& p) {
            if (aFull.expired())
                return;
            if (!mFullTouched) // a manual X press wins over the auto cloud-default
                mFullCloud = ok && MadJson::getBool(p, "connected", false);
            mFullResolved = true;
            deferRelayout([this] { rebuild(); });
        });
    }
}

void GuiMadPageBackup::rebuild()
{
    if (mSection == Section::Landing) {
        rebuildLanding();
        return;
    }
    beginColumn();
    mChipRows.clear();
    {
        // Full backup: a destination bar (press X to swap On-this-Deck <-> MEGA) + ONLY that destination's
        // controls - the local whole-config archive OR the MEGA half, never both. (Section::Local is no
        // longer reached from the landing; it falls here too.)
        const float smallH {Font::get(FONT_SIZE_SMALL)->getHeight()};
        std::string bar {"Backing up to:  "};
        if (!mFullResolved)
            bar += "(checking MEGA…)";
        else {
            bar += mFullCloud ? "MEGA cloud" : "On this Deck";
            bar += "      (X: switch to " + std::string(mFullCloud ? "On this Deck" : "MEGA") + ")";
        }
        addBlock(bar, FONT_SIZE_SMALL, MadTheme::color(MadColor::Title), smallH * 0.3f);
        if (mFullResolved) {
            if (mFullCloud)
                buildCloudSection();
            else
                buildLocalSections();
        }
    }
    endColumn();
}

void GuiMadPageBackup::rebuildLanding()
{
    // Re-run on transfer-state changes (a transfer starts/ends) to add/remove the Ongoing tile.
    if (mGrid != nullptr) {
        mGridCookie = mGrid->cursorIndex();
        removeChild(mGrid.get());
        mGrid.reset();
    }

    std::vector<MadTileGrid::Tile> tiles;
    // Labels only (no sublabels): the pixel theme's narrow tiles clipped the longer sublabels, so
    // the tile name carries the meaning (the icon + the section content make it clear).
    // ORDER: every BACKUP destination leads, cheapest-scope first - Games, Emulator config, ES-DE
    // settings, BIOS, System, Controller config - then the whole-config Full backup, then Manage
    // backups. Restore is LAST: it is the hub you go looking for deliberately, not a step in the
    // backup sweep, so it no longer splits the category tiles from the whole-config ones.
    // The conditional Transfers tile still trails everything while a transfer is live.
    MadTileGrid::Tile granBackup;
    granBackup.key = "granbackup";
    granBackup.label = "Games";
    granBackup.artPath = MadTheme::routerIconPath("per-game");
    tiles.emplace_back(granBackup);

    // Emulator config + data backup (per-emulator; restore lives in the Restore hub).
    MadTileGrid::Tile emu;
    emu.key = "emucfg";
    emu.label = "Emulator config";
    emu.artPath = MadTheme::routerIconPath("backup-emu");
    tiles.emplace_back(emu);

    // ES-DE settings backup (grouped; restore lives in the Restore hub, staged to next boot per rule #3).
    MadTileGrid::Tile esde;
    esde.key = "esdesettings";
    esde.label = "ES-DE settings";
    esde.artPath = MadTheme::routerIconPath("backup-settings");
    tiles.emplace_back(esde);

    // Per-system BIOS backup & restore (its own bucket tiles -> file lists).
    MadTileGrid::Tile bios;
    bios.key = "bios";
    bios.label = "BIOS";
    bios.artPath = MadTheme::routerIconPath("backup-bios");
    tiles.emplace_back(bios);

    // System config backup (control-panel calibration, lightgun cal, Samba/backup prefs, EmuDeck settings);
    // restore lives in the Restore hub. LIVE restore (P12a).
    MadTileGrid::Tile system;
    system.key = "system";
    system.label = "System";
    system.artPath = MadTheme::routerIconPath("backup-system");
    tiles.emplace_back(system);

    // Controller config: back up / restore your controller setup + the on-device revert ops. Last of
    // the granular category tiles, before the whole-config Full backup.
    MadTileGrid::Tile controllers;
    controllers.key = "controllers";
    controllers.label = "Controller config";
    controllers.artPath = MadTheme::routerIconPath("backup-controllers");
    tiles.emplace_back(controllers);

    // Full backup: the whole-config backup on ONE page - press X to switch On-this-Deck <-> MEGA (only that
    // destination's controls show; never both). The old separate "Local" tile is retired; its archive
    // controls live here. (key stays "cloud" -> the Section::Cloud page.)
    MadTileGrid::Tile full;
    full.key = "cloud";
    full.label = "Full backup";
    full.artPath = MadTheme::routerIconPath("backup-local");
    tiles.emplace_back(full);

    // Manage backups: browse every backup set (local + MEGA, all categories) and PERMANENTLY delete
    // the ones you no longer want.
    MadTileGrid::Tile manage;
    manage.key = "manage";
    manage.label = "Manage backups";
    manage.artPath = MadTheme::routerIconPath("backup-manage");
    tiles.emplace_back(manage);

    // Restore is a HUB: pick a category (Games / Settings / BIOS / System) to restore. LAST tile - see
    // the ORDER note above.
    MadTileGrid::Tile granRestore;
    granRestore.key = "granrestore";
    granRestore.label = "Restore";
    granRestore.artPath = MadTheme::routerIconPath("backup-restore");
    tiles.emplace_back(granRestore);

    // The transfers tile is present while ANY registered transfer is live - the panel's own
    // op (mCloudProgress), the game-end hook push, a CLI run, or a detached transfer that
    // survived a panel restart (mLiveTransfers, from transfers.list via fetchActive).
    // "Transfers" stays short to avoid clipping.
    const bool transferLive {(mCloudProgress != nullptr && mCloudProgress->active &&
                              !mCloudProgress->done) ||
                             mRoot->mLiveTransfers > 0};
    if (transferLive) {
        MadTileGrid::Tile ongoing;
        ongoing.key = "ongoing";
        ongoing.label = "Transfers";
        ongoing.artPath = MadTheme::routerIconPath("backup-ongoing-transfers");
        tiles.emplace_back(ongoing);
    }

    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mViewportPos.y);
    mGrid->setSize(mViewportSize.x, mViewportSize.y);
    mGrid->setTiles(tiles);
    mGrid->setCursorIndex(mGridCookie);
    mGrid->setOnPick([this](const std::string& key) {
        if (key == "controllers")
            // Controller config: its OWN backup category page (bar + groups + backup/restore + revert ops).
            mPanel->pushPage(new GuiMadPageControllers(mPanel, "backup"));
        else if (key == "cloud")
            // Full backup: the whole-config local archive + the MEGA cloud half, one page (Section::Cloud).
            mPanel->pushPage(new GuiMadPageBackup(mPanel, this, Section::Cloud));
        else if (key == "granbackup")
            // Games backup: the destination (folder / MEGA) is a bar at the TOP of the systems page.
            mPanel->pushPage(new GuiMadPageBackupRestore(mPanel, "backup"));
        else if (key == "granrestore")
            // Restore is a HUB: pick a category (Games / Settings / BIOS) to restore. Each Backup tile only
            // backs UP; restoring anything goes through here.
            mPanel->pushPage(new GuiMadPageRestoreHub(mPanel));
        else if (key == "manage")
            // Manage backups: a category hub (All + per-category) -> that category's set list -> delete.
            mPanel->pushPage(new GuiMadPageManage(mPanel));
        else if (key == "bios")
            // BIOS backup: the destination bar is at the TOP of the bucket page.
            mPanel->pushPage(new GuiMadPageBios(mPanel, "backup"));
        else if (key == "esdesettings")
            // ES-DE settings BACKUP (grouped; the destination bar is at the TOP of the page). Restore lives
            // in the Restore hub (staged to next boot, rule #3).
            mPanel->pushPage(new GuiMadPageEsde(mPanel, "backup"));
        else if (key == "system")
            // System config BACKUP (grouped; the destination bar is at the TOP of the page). Restore lives
            // in the Restore hub (LIVE restore, rule #5).
            mPanel->pushPage(new GuiMadPageSystem(mPanel, "backup"));
        else if (key == "emucfg")
            // Emulator config BACKUP (per-emulator; the destination bar is at the TOP of the tile page).
            mPanel->pushPage(new GuiMadPageEmu(mPanel, "backup"));
        else if (key == "ongoing")
            mPanel->pushPage(new GuiMadPageCloudProgress(
                mPanel, mCloudOpTitle.empty() ? "Transfer progress" : mCloudOpTitle,
                mCloudProgress));
    });
    mGrid->onFocusGained(); // the grid is this page's only focusable
    addChild(mGrid.get());
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBackup::buildLocalSections()
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    header("Backup destination");
    caption("Where RUN FULL BACKUP writes its archive. Pick any folder "
            "on the internal drive, the SD card, or a USB drive.");
    mDestLabel = addBlock("  Saving to: " + destDisplay(), FONT_SIZE_SMALL,
                          MadTheme::color(MadColor::Title), smallHeight * 0.3f);
    addButton("CHANGE DESTINATION", [this] { openDestPicker(); });
    if (mRoot->mBackupDest.empty())
        fetchDest();

    header("Full backup");
    caption("Archive your whole setup — toggle what to include, then run (keep MAD open until it "
            "finishes). ROMs (internal) + OpenBOR sit on that same internal drive, so include them "
            "only if you copy the backup off-device.");
    for (const auto& row : CATEGORY_ROWS) {
        std::vector<MadChipRow::Chip> chips;
        for (const Category& category : row)
            chips.push_back({category.key, chipLabel(category.key),
                             mRoot->mInclude.at(category.key)});
        auto chipRow = addChips(chips, false);
        chipRow->setOnToggle([this](const std::string& key, const bool on) {
            mRoot->mInclude[key] = on; // durable on the root: survives leaving/re-opening Local
            updateTally();
        });
        mChipRows.emplace_back(chipRow);
    }
    // ROMs are per-game now, not an all-or-nothing toggle: pick systems -> games. What you choose is
    // backed up per-game (with a manifest) so you can restore individual games from the Restore tile.
    caption("ROMs: choose which games to include (systems -> games). They are backed up per-game so you "
            "can restore them one at a time.");
    mGamesLabel = addBlock("  ROMs: " + gamesCountLabel(), FONT_SIZE_SMALL,
                           MadTheme::color(MadColor::Title), smallHeight * 0.3f);
    addButton("CHOOSE GAMES", [this] { openGamesPicker(); });
    // Backup format: gzip (.tar.gz, default) / store (.tar) / mirror (a browsable folder tree you can
    // open in a file manager). A-pressable choice row (per the choice-row standing rule) rather than a
    // switch. ROMs/media stay .tar unless you pick mirror, in which case they mirror to folders too.
    caption("Config + saves are written as a compressed archive, a plain archive, or a browsable "
            "folder you can open directly in a file manager (ROMs/media become folders only in that "
            "mode).");
    mFormatLabel = addBlock("  Format: " + formatDisplay(), FONT_SIZE_SMALL,
                            MadTheme::color(MadColor::Title), smallHeight * 0.3f);
    addButton("CHANGE FORMAT", [this] { pickFormat(); });
    if (!mRoot->mFormatLoaded)
        fetchFormat();
    // Placeholder text BEFORE the height is measured — an empty block
    // autosizes to ~0 and the button below would overlap the tally.
    mTally = addBlock("  Total selected: …", FONT_SIZE_SMALL, MadTheme::color(MadColor::Title),
                      smallHeight * 0.3f);
    updateTally();
    addButton("RUN FULL BACKUP NOW", [this] { mRoot->runFull(mRoot->mInclude); });
}


void GuiMadPageBackup::buildCloudSection()
{
    header("Cloud backup (MEGA)");
    if (!mCloudStatusLoaded) {
        caption("Checking your MEGA connection…");
        return;
    }

    if (mCloudConnected) {
        std::string line {"Connected.  Server: " + mCloudServerLabel};
        if (!mCloudLastBackup.empty())
            line += "   Last save backup: " + mCloudLastBackup;
        caption(line);
    }
    if (mCloudConnected) {
        // Same per-game choice the LOCAL full backup has had: without it a cloud full backup could
        // never include games, which is not a distinction the two pages should have.
        caption("ROMs: choose which games to include (systems -> games). They are backed up per-game "
                "so you can restore them one at a time.");
        mCloudGamesLabel = addBlock("  ROMs: " + gamesCountLabel(), FONT_SIZE_SMALL,
                                    MadTheme::color(MadColor::Title),
                                    Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f);
        addButton("CHOOSE GAMES", [this] { openGamesPicker(); });
    }
    else {
        caption("Not connected. Create MEGA S4 access keys once, drop the file on the "
                "Deck, then connect - no typing needed.");
        addButton("CONNECT TO MEGA…", [this] {
            // Keys are LONG random strings: never typed here. The dialog explains how to
            // create + place the file; CONNECT runs the idempotent setup (writes the
            // rclone remote, probes) and refreshes the page.
            std::weak_ptr<int> alive {pageAlive()};
            mWindow->pushGui(new MadMsgBox(
                "1)  On mega.io open  S4 > Access keys  and create a key pair.\n"
                "2)  Save it on this Deck as  ~/.ssh/credentials-steamdeck\n"
                "     (two lines - e.g. copy the file over the Samba share):\n"
                "         aws_access_key_id=YOURKEY\n"
                "         aws_secret_access_key=YOURSECRET\n"
                "3)  Press CONNECT.",
                "CONNECT",
                [this, alive] {
                    if (alive.expired())
                        return;
                    footer()->setStatus("Connecting to MEGA…");
                    std::weak_ptr<int> a2 {pageAlive()};
                    pageRequest(
                        "cloud.connect_setup", nullptr,
                        [this, a2](bool ok, const rapidjson::Value& p) {
                            if (a2.expired())
                                return;
                            footer()->setStatus("");
                            const bool connected {ok && MadJson::getBool(p, "connected")};
                            footer()->flash(
                                connected ? "Connected to MEGA."
                                          : "Not connected: " +
                                                MadJson::getString(p, "message",
                                                                   "keys not found yet"),
                                7000, !connected);
                            fetchCloud(); // refresh the section either way
                        },
                        200000);
                },
                "CLOSE", [] {}));
        });
    }

    // Server picker: an A-pressable list of the MEGA S4 servers. All reach the
    // same files — the choice only changes the route (upload speed). Shown once
    // the server list has arrived.
    if (mCloudServersLoaded && !mCloudServers.empty()) {
        addButton("MEGA SERVER:  " + mCloudServerLabel, [this] {
            if (busyGuard())
                return;
            pickServer();
        });
    }

    // Own toggles: WHAT the cloud backs up, in two tiers. "Back up now" + the auto backups
    // honor the Tier-A chips; "Sync library now" honors the Tier-B chips. (MAD/router config
    // and the saved memory are always included.) Shown once cloud.categories has arrived.
    if (mCloudCatsLoaded) {
        const float smallH {Font::get(FONT_SIZE_SMALL)->getHeight()};
        caption("Back up (saves + configs) — included on exit and when you press Back up now:");
        std::vector<MadChipRow::Chip> a;
        for (const auto& c : mCatA)
            a.push_back({c.first, cloudCatLabel(c.first, c.second),
                         mCatOn.count(c.first) ? mCatOn[c.first] : true});
        mCatRowA = addChips(a, false);
        mCatRowA->setOnToggle([this](const std::string& key, const bool on) { setCategory(key, on); });
        mCloudTallyA = addBlock("  Selected: …", FONT_SIZE_MINI, MadTheme::color(MadColor::Title),
                                smallH * 0.2f);

        caption("Library (large, re-downloadable) — included only in Sync library now:");
        std::vector<MadChipRow::Chip> b;
        for (const auto& c : mCatB)
            b.push_back({c.first, cloudCatLabel(c.first, c.second),
                         mCatOn.count(c.first) ? mCatOn[c.first] : true});
        mCatRowB = addChips(b, false);
        mCatRowB->setOnToggle([this](const std::string& key, const bool on) { setCategory(key, on); });
        mCloudTallyB = addBlock("  Selected: …", FONT_SIZE_MINI, MadTheme::color(MadColor::Title),
                                smallH * 0.2f);
        updateCloudTally();
    }

    // (Per-game cloud upload lives on the Games tile, which already targets MEGA - not duplicated here.)

    // The when-to-back-up toggles moved OUT of this tile: they are GLOBAL (they govern
    // transfers from EVERY tile, not just this one), so they live in the ES-DE main menu
    // where global behavior belongs. The old "keep syncing during play" timer is gone
    // entirely - transfers are persistent jobs now, frozen/thawed around gameplay by the
    // BACKUP DURING GAMEPLAY switch.
    caption("Backup behavior (during gameplay / auto resume / on exit) is set in the ES-DE "
            "main menu under Other settings.");

    // A live transfer's progress is reachable from the Landing's "Ongoing transfers" tile (and the
    // subpage auto-opens when an op starts), so no in-page "View progress" button is needed here.
    header("Actions");
    addButtonRow(
        {{"BACK UP NOW",
          [this] {
              if (cloudGuard())
                  return;
              mRoot->startCloudOp("cloud.push", "Backing up saves", nullptr,
                                  "Saves backed up to MEGA.", this, pageAlive());
          }},
         {"SYNC LIBRARY NOW", [this] {
              if (cloudGuard())
                  return;
              confirmThen("Sync the selected library folders (ROMs/media/...) to MEGA now? Large, "
                          "one-off upload — best done plugged in. It never deletes at MEGA.",
                          [this] {
                              mRoot->startCloudOp("cloud.sync", "Syncing library", nullptr,
                                                  "Library synced to MEGA.", this, pageAlive());
                          });
          }}});
    addButtonRow(
        {{"RESTORE SAVES…",
          [this] {
              if (cloudGuard())
                  return;
              openRestorePicker(); // pick "latest" or a dated rollback, then confirm + restore
          }},
         {"RESTORE LIBRARY…", [this] {
              if (cloudGuard())
                  return;
              openRestoreLibrary();
          }}});
}

// cloud.status ONLY (cheap, no size walk): connection + server label + the on-exit/timer/auto-resume
// toggles + the last-backup time. Split out so onChildPopped can refresh the "Last save backup" line
// when the Cloud subpage is revealed after a transfer, without re-triggering the slow cloud.sizes walk.
void GuiMadPageBackup::fetchCloudStatus()
{
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest("cloud.status", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired())
                        return;
                    mCloudStatusLoaded = true;
                    if (ok) {
                        mCloudConnected = MadJson::getBool(payload, "connected");
                        mCloudServerId = MadJson::getString(payload, "server", "global");
                        mCloudServerLabel = MadJson::getString(payload, "server_label", mCloudServerId);
                        mCloudLastBackup = MadJson::getString(payload, "last_backup", "");
                    }
                    deferRelayout([this] { rebuild(); });
                },
                30000);
}

void GuiMadPageBackup::fetchCloud()
{
    fetchCloudStatus();
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest("cloud.servers", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired())
                        return;
                    mCloudServersLoaded = true;
                    if (ok) {
                        mCloudServers.clear();
                        const rapidjson::Value& arr {MadJson::getMember(payload, "servers")};
                        if (arr.IsArray()) {
                            for (const rapidjson::Value& s : arr.GetArray()) {
                                const std::string id {MadJson::getString(s, "id")};
                                if (!id.empty())
                                    mCloudServers.emplace_back(
                                        id, MadJson::getString(s, "label", id));
                            }
                        }
                    }
                    deferRelayout([this] { rebuild(); });
                },
                30000);
    pageRequest("cloud.categories", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired())
                        return;
                    mCloudCatsLoaded = true;
                    if (ok) {
                        mCatA.clear();
                        mCatB.clear();
                        auto load = [&](const char* key,
                                        std::vector<std::pair<std::string, std::string>>& out) {
                            const rapidjson::Value& arr {MadJson::getMember(payload, key)};
                            if (!arr.IsArray())
                                return;
                            for (const rapidjson::Value& c : arr.GetArray()) {
                                const std::string k {MadJson::getString(c, "key")};
                                if (k.empty())
                                    continue;
                                out.emplace_back(k, MadJson::getString(c, "label", k));
                                mCatOn[k] = MadJson::getBool(c, "on");
                            }
                        };
                        load("tierA", mCatA);
                        load("tierB", mCatB);
                    }
                    deferRelayout([this] { rebuild(); });
                },
                30000);

    // Tier-A post-filter sizes (what the cloud actually uploads). Slow (~10-12 s of rclone
    // size walks), so it lands after the chips already render; the chips show "(calculating…)"
    // until then. On failure we still clear the flag so the suffix doesn't hang forever.
    pageRequest("cloud.sizes", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired())
                        return;
                    mCloudSizesDone = true;
                    if (ok) {
                        const rapidjson::Value& sizes {MadJson::getMember(payload, "sizes")};
                        if (sizes.IsObject()) {
                            for (auto it = sizes.MemberBegin(); it != sizes.MemberEnd(); ++it) {
                                if (it->value.IsInt64())
                                    mCloudSizes[it->name.GetString()] = it->value.GetInt64();
                            }
                        }
                    }
                    deferRelayout([this] { rebuild(); });
                },
                200000);
}

void GuiMadPageBackup::pickServer()
{
    std::weak_ptr<int> alive {pageAlive()};
    mPanel->pushPage(new GuiMadPageBackendChoice(
        mPanel, "MEGA server",
        "All servers reach the same files — this only changes the route (upload speed).",
        mCloudServers, mCloudServerId, [this, alive](const std::string& id) {
            if (!alive.expired())
                setServer(id);
        }));
}

void GuiMadPageBackup::openRestorePicker()
{
    std::weak_ptr<int> alive {pageAlive()};
    // Fetch the dated rollback points, then let the user pick "latest" (the whole backup) or a
    // version folder. If the list can't be fetched we still offer "latest".
    pageRequest(
        "cloud.snapshots", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok)
                footer()->flash("Couldn't load the version list — only Latest is available.", 4000,
                                false);
            std::vector<std::pair<std::string, std::string>> choices;
            choices.emplace_back("latest", "Latest  (the whole current backup)");
            if (ok && payload.HasMember("snapshots") && payload["snapshots"].IsArray()) {
                for (const auto& snap : payload["snapshots"].GetArray()) {
                    const std::string id {MadJson::getString(snap, "id")};
                    if (id.empty())
                        continue;
                    choices.emplace_back(
                        id, MadJson::getString(snap, "time", id) + "  (rollback of that run)");
                }
            }
            mPanel->pushPage(new GuiMadPageBackendChoice(
                mPanel, "Restore which version?",
                "Latest restores the whole backup. A dated version holds ONLY the previous copies "
                "of files changed at that time — a per-file rollback, not a full snapshot.",
                choices, "latest", [this, alive](const std::string& id) {
                    if (!alive.expired())
                        confirmRestore(id);
                }));
        },
        120000);
}

void GuiMadPageBackup::confirmRestore(const std::string& snapshot)
{
    std::string msg;
    if (snapshot == "latest") {
        msg = "Restore your saves + emulator configs from MEGA over the live ones? Overwritten "
              "files go to a recoverable _TMP first (nothing is deleted) and the MAD tooling is "
              "untouched. Your ES-DE + controller settings are staged and applied when ES-DE "
              "restarts (you'll be offered a restart). Close your emulators first.";
    }
    else {
        // "20260723-071500" -> "2026-07-23 07:15:00" for readability (raw id if it doesn't match).
        std::string when {snapshot};
        if (snapshot.size() == 15 && snapshot[8] == '-')
            when = snapshot.substr(0, 4) + "-" + snapshot.substr(4, 2) + "-" + snapshot.substr(6, 2) +
                   " " + snapshot.substr(9, 2) + ":" + snapshot.substr(11, 2) + ":" +
                   snapshot.substr(13, 2);
        msg = "Restore the " + when + " rollback over your live files? It holds ONLY the previous "
              "copies of files changed in that run — a per-file rollback, NOT a full snapshot. "
              "Overwritten files go to a recoverable _TMP first. Close your emulators first.";
    }
    confirmThen(msg, [this, snapshot] {
        mRoot->startCloudOp(
            "cloud.restore_precious", "Restoring saves",
            [snapshot](MadJson::Writer& writer) {
                writer.Key("to_live");
                writer.Bool(true);
                writer.Key("snapshot");
                writer.String(snapshot.c_str(), static_cast<rapidjson::SizeType>(snapshot.length()));
            },
            "Saves + emulator configs restored.", this, pageAlive(), /*offerRestart=*/true);
    });
}

void GuiMadPageBackup::setServer(const std::string& id)
{
    if (id == mCloudServerId)
        return; // no change — skip the network probe
    std::weak_ptr<int> alive {pageAlive()};
    footer()->setStatus("Switching MEGA server…");
    pageRequest(
        "cloud.set_server",
        [id](MadJson::Writer& writer) {
            writer.Key("server");
            writer.String(id.c_str(), static_cast<rapidjson::SizeType>(id.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            footer()->setStatus("");
            footer()->flash(
                MadJson::getString(payload, "message", ok ? "Server changed." : "Could not change server."),
                6000, !ok);
            if (ok)
                fetchCloud(); // refresh the status line + server label
        },
        // set_server runs a reachability probe on the new server (up to ~45s).
        90000);
}

bool GuiMadPageBackup::cloudGuard()
{
    if (busyGuard())
        return true;
    if (!mCloudConnected) {
        footer()->flash("Not connected to MEGA — run the cloud setup in Desktop Mode.", 4000, true);
        return true;
    }
    return false;
}

void GuiMadPageBackup::onChildPopped()
{
    // Returning to the Landing rebuilds the grid so the Ongoing-transfers tile matches whether a
    // cloud transfer is still live; returning to a subpage refreshes its column (e.g. after a
    // picker). Deferred because the revealed page is now current, so its update() will run it.
    // The Cloud subpage also re-pulls cloud.status (cheap) so its "Last save backup" line reflects a
    // transfer that just finished in the progress subpage on top of it.
    if (mSection == Section::Cloud)
        fetchCloudStatus();
    // NOTE: the Landing's registry re-check lives in onRestoreFocus, which popPage() calls
    // immediately before this - issuing fetchActive here too would double cloud.active AND
    // the slow-pool transfers.list (which runs the registry housekeeping) on every pop.
    deferRelayout([this] { rebuild(); });
}

bool GuiMadPageBackup::input(InputConfig* config, Input input)
{
    if (mSection == Section::Landing)
        return mGrid != nullptr && mGrid->input(config, input);
    // Full backup: X swaps the destination (On-this-Deck <-> MEGA) once the cloud probe has resolved; the
    // page then re-renders with only that destination's controls.
    if (mSection == Section::Cloud && mFullResolved && input.value != 0 && config->isMappedTo("x", input)) {
        mFullTouched = true;
        mFullCloud = !mFullCloud;
        deferRelayout([this] { rebuild(); });
        return true;
    }
    return MadLightgunPageBase::input(config, input);
}

void GuiMadPageBackup::pageScroll(int direction)
{
    if (mSection == Section::Landing) {
        if (mGrid != nullptr)
            mGrid->pageScroll(direction);
        return;
    }
    MadLightgunPageBase::pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageBackup::getHelpPrompts()
{
    if (mSection == Section::Landing)
        return mGrid != nullptr ? mGrid->getHelpPrompts() : std::vector<HelpPrompt> {};
    return MadLightgunPageBase::getHelpPrompts();
}

void GuiMadPageBackup::onSaveFocus()
{
    if (mSection == Section::Landing) {
        if (mGrid != nullptr)
            mGridCookie = mGrid->cursorIndex();
        return;
    }
    MadLightgunPageBase::onSaveFocus();
}

void GuiMadPageBackup::onRestoreFocus()
{
    if (mSection == Section::Landing) {
        if (mGrid != nullptr) {
            mGrid->setCursorIndex(mGridCookie);
            mGrid->onFocusGained();
        }
        // Adopt a transfer that started while we were away - e.g. a granular cloud backup the user began
        // in a BIOS/game subpage and then backed out of. offerResume=false: only adopt, never re-prompt
        // the resume-restore modal on a plain return to the Landing.
        fetchActive(false);
        return;
    }
    MadLightgunPageBase::onRestoreFocus();
}

void GuiMadPageBackup::setCategory(const std::string& key, const bool on)
{
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "cloud.set_category",
        [key, on](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("value");
            writer.String(on ? "on" : "off");
        },
        [this, alive, key, on](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (ok) {
                mCatOn[key] = on;
                // Re-sync the switch (a rebuild may have recreated the row); the row that
                // doesn't hold this key just no-ops.
                if (mCatRowA != nullptr)
                    mCatRowA->setChipState(key, on);
                if (mCatRowB != nullptr)
                    mCatRowB->setChipState(key, on);
                updateCloudTally();
                footer()->flash(MadJson::getString(payload, "message", "Saved."), 2500, false);
            }
            else {
                if (mCatRowA != nullptr)
                    mCatRowA->setChipState(key, !on); // revert the optimistic flip
                if (mCatRowB != nullptr)
                    mCatRowB->setChipState(key, !on);
                footer()->flash(MadJson::getString(payload, "message", "Could not change it."),
                                5000, true);
            }
        },
        20000);
}

void GuiMadPageBackup::fillProgress(const rapidjson::Value& prog)
{
    if (mCloudProgress == nullptr)
        return;
    CloudProgress& p {*mCloudProgress};
    p.overallFrac = static_cast<float>(MadJson::getInt(prog, "overall_pct", 0)) / 100.0f;
    p.transfers.clear();
    const rapidjson::Value& arr {MadJson::getMember(prog, "transfers")};
    if (arr.IsArray()) {
        for (const rapidjson::Value& t : arr.GetArray()) {
            const int pct {MadJson::getInt(t, "pct", 0)};
            std::string name {MadJson::getString(t, "name")};
            const size_t slash {name.find_last_of('/')}; // show just the file's tail
            if (slash != std::string::npos)
                name = name.substr(slash + 1);
            p.transfers.push_back(
                {name + "   " + std::to_string(pct) + "%", static_cast<float>(pct) / 100.0f});
        }
    }
}

void GuiMadPageBackup::startCloudOp(const std::string& method, const std::string& title,
                                    const MadJson::ParamsWriter& params, const std::string& okMsg,
                                    MadPage* progressHost, const std::weak_ptr<int>& hostAlive,
                                    bool offerRestart)
{
    // Runs in the ROOT's context (a Cloud subpage calls mRoot->startCloudOp), so mRunning/mRunToken/
    // mCloudProgress + the stream all live on the durable Landing and survive popping the subpage.
    if (mRunning) {
        footer()->flash("Another job is already running.", 3000, true);
        return;
    }
    mRunning = true; // claim the guard SYNCHRONOUSLY (before the async response) so a full backup
                     // and a cloud op can't both slip through the request window.
    mCloudOpTitle = title; // so the Ongoing-transfers tile can re-open this op's subpage
    // Reset the shared progress; the root owns the stream and keeps filling mCloudProgress, the
    // progress subpage just renders it. Leaving the subpage (B) does NOT kill the job.
    *mCloudProgress = CloudProgress {};
    mCloudProgress->active = true;
    mCloudProgress->overallLabel = "Starting…";
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        method, params,
        [this, alive, title, okMsg, progressHost, hostAlive, offerRestart](
            bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                mRunning = false; // release the sync guard; the op never started
                mCloudProgress->active = false;
                footer()->flash("Couldn't start: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                5000, true);
                // Drop any Ongoing-transfers tile the optimistic active=true may have shown, exactly
                // like the done/closed terminal paths - else a phantom tile lingers on the Landing.
                deferRelayout([this] { rebuild(); });
                return;
            }
            // QUEUED: the engine was busy, so this op is waiting its turn. There is no stream to
            // attach to yet, so release the synchronous guard and say where it landed - treating it
            // as a failure (the old behaviour for a busy engine) would tell the user it did not
            // start when in fact it is safely in the queue.
            if (payload.HasMember("queued")) {
                mRunning = false;
                mCloudProgress->active = false;
                const long long pos {MadJson::getInt64(payload, "position", 0)};
                footer()->flash(title + " queued" +
                                    (pos > 1 ? " (" + std::to_string(pos) + " ahead of it)" : "") +
                                    " — it starts when the current transfer finishes.",
                                5000, false);
                deferRelayout([this] { rebuild(); });   // surface the Transfers tile
                return;
            }
            footer()->setStatus(title + "…");
            // Open the live progress onto the subpage the user launched from (if still on top).
            if (progressHost != nullptr && !hostAlive.expired() &&
                mPanel->isCurrentPage(progressHost))
                mPanel->pushPage(new GuiMadPageCloudProgress(mPanel, title, mCloudProgress));
            installRunStream(MadJson::getString(payload, "stream"), okMsg, offerRestart);
        },
        30000);
}

void GuiMadPageBackup::installRunStream(const std::string& token, const std::string& okMsg,
                                        bool offerRestart)
{
    // Attach (or re-attach) to a running cloud op's stream. Always runs on the ROOT; the callback
    // captures the root's alive token so it keeps filling mCloudProgress even after the launching
    // subpage / progress subpage is popped.
    mRunToken = token;
    if (token.empty())
        return;
    std::weak_ptr<int> alive {pageAlive()};
    backend()->setStreamCallback(token, [this, alive, okMsg, offerRestart](
                                            const rapidjson::Value& data) {
        if (alive.expired())
            return;
        if (MadJson::getBool(data, "closed")) {
            if (mRunning) {
                mRunning = false;
                mCloudProgress->done = true;
                mCloudProgress->rc = -1;
                footer()->setStatus("");
                footer()->flash("The job ended unexpectedly.", 5000, true);
                deferRelayout([this] { rebuild(); }); // drop the Ongoing-transfers tile
            }
            return;
        }
        if (MadJson::getBool(data, "done")) {
            if (!mRunning)
                return; // idempotent (like 'closed'): a duplicate terminal 'done' must not
                        // re-fire the flash or stack a second restart modal.
            mRunning = false;
            const int rc {MadJson::getInt(data, "rc", -1)};
            mCloudProgress->done = true;
            mCloudProgress->rc = rc;
            footer()->setStatus("");
            if (rc == 0 && offerRestart) {
                // The precious restore staged ES-DE + launchers config; the launch wrapper applies
                // it on the NEXT start (before ES-DE reads its config), so offer a one-tap restart.
                // Mirror the F4 updater: RESTART re-execs the wrapper only when it is present.
                const bool madRestart {std::getenv("MAD_WRAPPER") != nullptr};
                mWindow->pushGui(new MadMsgBox(
                    "Restore complete. Your ES-DE settings and controller config are staged and "
                    "apply the next time ES-DE starts.\n\nRestart ES-DE now to apply them?",
                    madRestart ? "RESTART ES-DE" : "QUIT ES-DE",
                    [madRestart] {
                        Utils::Platform::quitES(madRestart ? Utils::Platform::QuitMode::RESTART
                                                           : Utils::Platform::QuitMode::QUIT);
                    },
                    "LATER", [] {}));
            }
            else if (rc == 0) {
                // A per-set push (cloud.push_games via _push_set) publishes its manifest and exits 0
                // even when some files failed to upload; `failed` (>0) rides the terminal so we warn
                // instead of claiming a clean success. Other ops (push/sync/restore) never emit it.
                const int failed {MadJson::getInt(data, "failed", 0)};
                if (failed > 0)
                    footer()->flash("Backed up to MEGA, but " + std::to_string(failed) +
                                        " file(s) failed to upload. Check the log.",
                                    9000, true);
                else
                    footer()->flash(okMsg, 8000, false);
            }
            else
                footer()->flash("FAILED (exit " + std::to_string(rc) + ").", 8000, true);
            deferRelayout([this] { rebuild(); }); // drop the Ongoing-transfers tile
            return;
        }
        if (data.HasMember("progress")) {
            fillProgress(data["progress"]);
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty()) {
            mCloudProgress->overallLabel = line;
            footer()->setStatus(line);
        }
    });
}

void GuiMadPageBackup::fetchActive(bool offerResume)
{
    // Landing reattach: if a transfer is already running - the game-end hook push, a
    // detached transfer surviving a panel restart, an auto-resumed upload, or a granular
    // cloud backup the user backed out of - adopt it so the Transfers tile + its progress
    // reflect it. `token` is only set when THIS daemon session started the op; otherwise
    // the job id rides `job` and we attach a tail stream via transfers.attach.
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "cloud.active", nullptr,
        [this, alive, offerResume](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || !ok)
                return;
            const bool running {MadJson::getBool(payload, "running")};
            if (running && !mRunning) {
                mRunning = true;
                mCloudOpTitle = MadJson::getString(payload, "title", "Transfer");
                *mCloudProgress = CloudProgress {};
                mCloudProgress->active = true;
                mCloudProgress->paused = MadJson::getBool(payload, "paused");
                mCloudProgress->overallLabel =
                    mCloudProgress->paused ? "Paused" : "Reattaching…";
                // A reattached RESTORE staged config, so it still wants the restart prompt; a
                // reattached push/sync does not (title "Backing up…"/"Syncing…"). The precious
                // restore's title is "Restoring saves"; a library restore ("Restoring <cat>") is
                // harmless to offer (the wrapper no-ops when no config was staged).
                const bool reattachRestore {mCloudOpTitle.rfind("Restoring", 0) == 0};
                const std::string token {MadJson::getString(payload, "token")};
                if (!token.empty()) {
                    installRunStream(token, "Transfer finished.", reattachRestore);
                }
                else {
                    // Started before this daemon session (detached survivor / hook push):
                    // mint a tail stream over its .out via transfers.attach.
                    const std::string jobId {MadJson::getString(payload, "job")};
                    std::weak_ptr<int> a2 {pageAlive()};
                    pageRequest(
                        "transfers.attach",
                        [jobId](MadJson::Writer& w) {
                            w.Key("id");
                            w.String(jobId.c_str(),
                                     static_cast<rapidjson::SizeType>(jobId.length()));
                        },
                        [this, a2, reattachRestore](bool aok, const rapidjson::Value& ap) {
                            if (a2.expired())
                                return;
                            // Adopt ONLY a job that is still live and gave us a stream. A
                            // job that ended between cloud.active and here (or a terminal
                            // one whose {done} the RPC layer may have dropped before our
                            // callback existed) must fully release the adoption, or the
                            // Transfers tile and a stuck "Reattaching…" progress page
                            // persist and every later backup is blocked by mRunning.
                            const std::string tok {aok ? MadJson::getString(ap, "stream") : ""};
                            const std::string st {MadJson::getString(ap, "state")};
                            if (!tok.empty() && (st == "running" || st == "paused")) {
                                installRunStream(tok, "Transfer finished.", reattachRestore);
                                return;
                            }
                            mRunning = false;
                            mCloudOpTitle.clear();
                            mCloudProgress->active = false;
                            mCloudProgress->done = true;
                            footer()->setStatus("");
                            deferRelayout([this] { rebuild(); }); // drop the phantom tile
                        },
                        30000);
                }
                deferRelayout([this] { rebuild(); }); // reveal the Transfers tile
            }
            // Only offer the restore-resume prompt when nothing is already running - and only on first
            // entry (offerResume), not on every Landing refocus (that would re-prompt each time back).
            if (offerResume && !running && MadJson::getBool(payload, "pending_restore"))
                promptResumeRestore();
        },
        30000);
    // The Transfers tile's gate: how many registered jobs are live RIGHT NOW (any source -
    // this panel, the game-end hook, a CLI run, a detached survivor). Cheap list; also runs
    // the registry housekeeping daemon-side.
    pageRequest(
        "transfers.list", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || !ok)
                return;
            int live {0};
            const rapidjson::Value& arr {MadJson::getMember(payload, "jobs")};
            if (arr.IsArray()) {
                for (const rapidjson::Value& j : arr.GetArray()) {
                    const std::string state {MadJson::getString(j, "state")};
                    // "queued" counts: a waiting transfer is work the user asked for, and a tile
                    // that only appeared once something started would hide the whole queue.
                    if (state == "running" || state == "paused" || state == "queued")
                        ++live;
                }
            }
            if (live != mRoot->mLiveTransfers) {
                mRoot->mLiveTransfers = live;
                deferRelayout([this] { rebuild(); }); // show/hide the Transfers tile
            }
        },
        30000);
}

void GuiMadPageBackup::promptResumeRestore()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "A restore was interrupted last session. Resume it?", "RESUME RESTORE",
        [this, alive] {
            if (alive.expired())
                return;
            std::weak_ptr<int> a2 {pageAlive()};
            pageRequest("cloud.resume_pending", nullptr,
                        [this, a2](bool ok, const rapidjson::Value& payload) {
                            if (a2.expired() || !ok)
                                return;
                            const std::string token {MadJson::getString(payload, "stream")};
                            if (token.empty() || mRunning)
                                return;
                            mRunning = true;
                            mCloudOpTitle = "Restoring";
                            *mCloudProgress = CloudProgress {};
                            mCloudProgress->active = true;
                            mCloudProgress->overallLabel = "Resuming restore…";
                            // A resumed pending op is definitively a restore (only restores set the
                            // pending marker), so it too offers the restart to apply staged config.
                            installRunStream(token, "Restore finished.", /*offerRestart=*/true);
                            deferRelayout([this] { rebuild(); });
                        });
        },
        "DISCARD", [this, alive] {
            if (alive.expired())
                return;
            pageRequest("cloud.cancel", nullptr, nullptr);
        }));
}

void GuiMadPageBackup::openRestoreLibrary()
{
    if (mCatB.empty()) {
        footer()->flash("No library categories available to restore.", 3000, true);
        return;
    }
    std::vector<std::pair<std::string, std::string>> opts {mCatB};
    std::weak_ptr<int> alive {pageAlive()};
    mPanel->pushPage(new GuiMadPageBackendChoice(
        mPanel, "Restore library",
        "Restores the chosen folder to its live location (rebuilds ~/ROMs; overwrites -> _TMP).",
        opts, "", [this, alive](const std::string& cat) {
            if (alive.expired())
                return;
            confirmThen("Restore '" + cat + "' from MEGA to its live location? Overwritten files "
                        "are moved to a recoverable _TMP first (nothing is deleted).",
                        [this, cat] {
                            mRoot->startCloudOp("cloud.restore_library", "Restoring " + cat,
                                         [cat](MadJson::Writer& w) {
                                             w.Key("category");
                                             w.String(cat.c_str(),
                                                      static_cast<rapidjson::SizeType>(cat.length()));
                                             w.Key("to_live");
                                             w.Bool(true);
                                         },
                                         "Library restored.", this, pageAlive());
                        });
        }));
}

bool GuiMadPageBackup::busyGuard()
{
    // While the full backup streams, its output lines own the footer (each
    // non-empty setStatus cancels flashes) and mixing file operations into a
    // running archive job is asking for trouble — park everything else.
    if (mRoot->mRunning) {
        // mRunning (on the root) covers the full backup AND the cloud push/sync/restore streams,
        // so keep this job-neutral (not "backup").
        footer()->flash("Wait for the running job to finish first.", 3000, true);
        return true;
    }
    return false;
}

MadBackend::ResponseCallback GuiMadPageBackup::resultFlash()
{
    return [this](bool ok, const rapidjson::Value& payload) {
        footer()->setStatus("");
        footer()->flash(MadJson::getString(payload, "message", "unknown error"), 5000, !ok);
    };
}

void GuiMadPageBackup::confirmThen(const std::string& text,
                                   const std::function<void()>& action)
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        text, "YES",
        [alive, action] {
            if (!alive.expired())
                action();
        },
        "CANCEL", nullptr));
}

void GuiMadPageBackup::updateTally()
{
    if (mTally == nullptr)
        return;
    long long total {0};
    bool calculating {false};
    for (const auto& entry : mRoot->mInclude) {
        if (!entry.second)
            continue; // not selected
        const auto it = mSizes.find(entry.first);
        if (it != mSizes.end())
            total += it->second;
        else if (!mSizesDone)
            calculating = true; // a SELECTED item's size hasn't arrived and the walk is still running
    }
    // Only "calculating" while a SELECTED item's size is still pending - not merely because the
    // size walk hasn't finished. Nothing selected (or all selected sizes known) -> no suffix.
    mTally->setText("  Total selected: " + human(total) +
                    (calculating ? "   (calculating…)" : ""));
}

void GuiMadPageBackup::onSizePush(const rapidjson::Value& data)
{
    if (MadJson::getBool(data, "closed")) {
        // Stream died without done (spawn failure / daemon restart): stop
        // claiming "(calculating…)" forever — show what we have.
        if (!mSizesDone) {
            mSizesDone = true;
            updateTally();
            updateCloudTally();
        }
        return;
    }
    if (MadJson::getBool(data, "done")) {
        mSizesDone = true;
        updateTally();
        updateCloudTally();
        return;
    }
    const std::string key {MadJson::getString(data, "key")};
    if (key.empty() || !data.HasMember("bytes") || !data["bytes"].IsInt64())
        return;
    mSizes[key] = data["bytes"].GetInt64();
    // Update the chip label in place; if the wider label re-wrapped a row, the
    // column heights are stale — rebuild on the next tick (focus is preserved
    // via the base class cookies; pushes between ticks coalesce).
    bool reflow {false};
    auto touch = [&](const std::shared_ptr<MadChipRow>& row, const std::string& lbl) {
        if (row == nullptr)
            return;
        const float before {row->contentHeight()};
        row->setChipLabel(key, lbl);
        if (row->contentHeight() != before)
            reflow = true;
    };
    for (const auto& chipRow : mChipRows)
        touch(chipRow, chipLabel(key));
    // The cloud tier chips share the same size data (same category keys).
    for (const auto& c : mCatA)
        if (c.first == key)
            touch(mCatRowA, cloudCatLabel(key, c.second));
    for (const auto& c : mCatB)
        if (c.first == key)
            touch(mCatRowB, cloudCatLabel(key, c.second));
    updateTally();
    updateCloudTally();
    if (reflow)
        deferRelayout([this] { rebuild(); });
}

void GuiMadPageBackup::runFull(const std::map<std::string, bool>& include)
{
    // Runs in the ROOT's context (the Local subpage calls mRoot->runFull) so the guard + the stream
    // outlive the transient Local subpage: the archive keeps going and the footer keeps updating
    // even after the user pops back to the Landing.
    if (mRunning) {
        footer()->flash("A full backup is already running.", 3000, true);
        return;
    }
    mRunning = true; // claim the guard synchronously (see startCloudOp) — one root, one mRunning.
    const std::string dest {mBackupDest}; // "" = engine default (~/deck-config-backups)
    const std::string fmt {mFormat};      // config-archive format: gzip | store | mirror
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "backup.run_full",
        [include, dest, fmt](MadJson::Writer& writer) {
            writer.Key("include");
            writer.StartObject();
            for (const auto& entry : include) {
                writer.Key(entry.first.c_str(),
                           static_cast<rapidjson::SizeType>(entry.first.length()));
                writer.Bool(entry.second);
            }
            writer.EndObject();
            if (!dest.empty()) {
                writer.Key("dest");
                writer.String(dest.c_str(), static_cast<rapidjson::SizeType>(dest.length()));
            }
            writer.Key("format");
            writer.String(fmt.c_str(), static_cast<rapidjson::SizeType>(fmt.length()));
        },
        [this, alive, dest](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                mRunning = false; // release the sync guard; the backup never started
                footer()->setStatus("");
                footer()->flash("Couldn't start: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                5000, true);
                return;
            }
            mRunToken = MadJson::getString(payload, "stream");
            footer()->setStatus("Backing up — keep MAD open until it finishes…");
            backend()->setStreamCallback(
                mRunToken, [this, alive, dest](const rapidjson::Value& data) {
                    if (alive.expired())
                        return;
                    if (MadJson::getBool(data, "closed")) {
                        if (mRunning) {
                            // Died without a done push (the backend always
                            // sends one, even on exceptions — this is the
                            // daemon-restart belt-and-braces).
                            mRunning = false;
                            footer()->setStatus("");
                            footer()->flash("Backup ended unexpectedly.", 5000, true);
                        }
                        return;
                    }
                    if (MadJson::getBool(data, "done")) {
                        const int rc {MadJson::getInt(data, "rc", -1)};
                        if (rc == 0 && !mGameSelection.empty()) {
                            // config archived OK -> now back up the chosen games into the SAME dest
                            // (mRunning stays true through this second phase). Detach THIS (config)
                            // stream first: the backend sends a trailing {closed} ~1ms after {done}, and
                            // if the config callback were still registered it would fire the "ended
                            // unexpectedly" branch and release mRunning mid-job. Safe from inside the
                            // callback (the dispatcher copied it before invoking).
                            backend()->clearStreamCallback(mRunToken);
                            footer()->setStatus("Config saved — backing up " +
                                                std::to_string(mGameSelection.size()) + " game(s)…");
                            runGamesBackup(dest);
                            return;
                        }
                        mRunning = false;
                        footer()->setStatus("");
                        footer()->flash(
                            rc == 0 ? "Full backup finished. Saved to " +
                                          (dest.empty() ? std::string {"~/deck-config-backups"}
                                                        : dest) +
                                          "."
                                    : "Backup FAILED (exit " + std::to_string(rc) + ").",
                            8000, rc != 0);
                        return;
                    }
                    const std::string line {MadJson::getString(data, "line")};
                    if (!line.empty())
                        footer()->setStatus(line); // Live progress in the help row.
                });
        },
        // Generous: a FAST restore ahead of us can hold the stdin thread for
        // many seconds on cold SD media before this request is even read.
        30000);
}

std::string GuiMadPageBackup::gamesCountLabel() const
{
    const size_t n {mRoot->mGameSelection.size()};
    return n == 0 ? "no games chosen"
                  : std::to_string(n) + (n == 1 ? " game chosen" : " games chosen");
}

void GuiMadPageBackup::openGamesPicker()
{
    // SELECT mode: the picker ticks games into mRoot->mGameSelection (a cross-system cart). The label
    // refreshes when it pops (onChildPopped). The chosen games are backed up on RUN FULL BACKUP (they are
    // chained into the archive as a per-game granular.backup). Driven by the Full backup page's CHOOSE GAMES.
    mPanel->pushPage(new GuiMadPageBackupRestore(mPanel, "select", &mRoot->mGameSelection));
}

std::vector<std::pair<std::string, std::string>> GuiMadPageBackup::itemsFromSelection() const
{
    // Split each "system:stem" id in the durable cart into a (system, stem) pair. The single reader of
    // mRoot->mGameSelection, used by the RUN FULL BACKUP -> runGamesBackup (granular.backup) chain.
    std::vector<std::pair<std::string, std::string>> items;
    for (const std::string& id : mRoot->mGameSelection) {
        const std::string::size_type colon {id.find(':')};
        if (colon != std::string::npos)
            items.emplace_back(id.substr(0, colon), id.substr(colon + 1));
    }
    return items;
}

void GuiMadPageBackup::runGamesBackup(const std::string& dest)
{
    // Chained after the config archive (runFull's done): stream a per-game backup of the chosen games
    // into the SAME dest, keeping mRunning true so the second phase is guarded + reported as one op.
    const auto items {itemsFromSelection()}; // (system, stem) pairs from the durable cart
    std::weak_ptr<int> alive {pageAlive()};
    // granular.backup_assets, NOT granular.backup: the latter plans ONE path per game (the ROM), so a
    // chosen game arrived in the backup without its box art or its saves while Backup -> Games copied
    // all of it. Sending no `keys` means "everything this game has", which is what the picker promises.
    pageRequest(
        "granular.backup_assets",
        [dest, items](MadJson::Writer& w) {
            if (!dest.empty()) {
                w.Key("dest");
                w.String(dest.c_str(), static_cast<rapidjson::SizeType>(dest.length()));
            }
            w.Key("items");
            w.StartArray();
            for (const auto& it : items) {
                w.StartObject();
                w.Key("system");
                w.String(it.first.c_str(), static_cast<rapidjson::SizeType>(it.first.length()));
                w.Key("stem");
                w.String(it.second.c_str(), static_cast<rapidjson::SizeType>(it.second.length()));
                w.EndObject();
            }
            w.EndArray();
        },
        [this, alive, dest](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                mRunning = false;
                footer()->setStatus("");
                footer()->flash("Config saved, but the per-game backup didn't start: " +
                                    MadJson::getString(payload, "message", "error"),
                                6000, true);
                return;
            }
            mRunToken = MadJson::getString(payload, "stream");
            if (mRunToken.empty()) { // defensive: no stream token would pin mRunning true forever
                mRunning = false;
                footer()->setStatus("");
                footer()->flash("Config saved, but the per-game backup didn't start.", 6000, true);
                return;
            }
            backend()->setStreamCallback(
                mRunToken, [this, alive, dest](const rapidjson::Value& data) {
                    if (alive.expired())
                        return;
                    if (MadJson::getBool(data, "closed")) {
                        if (mRunning) {
                            mRunning = false;
                            footer()->setStatus("");
                            footer()->flash("Per-game backup ended unexpectedly.", 5000, true);
                        }
                        return;
                    }
                    if (MadJson::getBool(data, "done")) {
                        mRunning = false;
                        footer()->setStatus("");
                        const int rc {MadJson::getInt(data, "rc", -1)};
                        if (rc != 0) {
                            footer()->flash("Config saved, but the per-game backup FAILED.", 6000, true);
                            return;
                        }
                        const int copied {MadJson::getInt(data, "copied", 0)};
                        const int skipped {MadJson::getInt(data, "skipped", 0)};
                        std::string msg {"Full backup finished — config + " + std::to_string(copied) +
                                         " game(s)"};
                        if (skipped > 0)
                            msg += " (" + std::to_string(skipped) + " skipped)";
                        msg += ". Saved to " +
                               (dest.empty() ? std::string {"~/deck-config-backups"} : dest) + ".";
                        footer()->flash(msg, 8000, false);
                        return;
                    }
                    const std::string line {MadJson::getString(data, "line")};
                    if (!line.empty())
                        footer()->setStatus(line);
                });
        },
        30000);
}

std::string GuiMadPageBackup::destDisplay() const
{
    return mRoot->mBackupDest.empty() ? std::string {"loading…"} : mRoot->mBackupDest;
}

std::string GuiMadPageBackup::formatDisplay() const
{
    const std::string& f {mRoot->mFormat};
    if (f == "store")
        return "Uncompressed archive (.tar)";
    if (f == "mirror")
        return "Browsable folder";
    return "Compressed archive (.tar.gz)";
}

void GuiMadPageBackup::pickFormat()
{
    std::weak_ptr<int> alive {pageAlive()};
    mPanel->pushPage(new GuiMadPageBackendChoice(
        mPanel, "Backup format",
        "Compressed is smallest; a browsable folder lets you open your saves directly in a file "
        "manager (ROMs/media also become folders in that mode).",
        {{"gzip", "Compressed archive (.tar.gz) — smaller, slower"},
         {"store", "Uncompressed archive (.tar) — faster, bigger"},
         {"mirror", "Browsable folder — open your files directly"}},
        mRoot->mFormat, [this, alive](const std::string& fmt) {
            if (!alive.expired())
                setFormat(fmt);
        }));
}

void GuiMadPageBackup::setFormat(const std::string& fmt)
{
    mRoot->mFormat = fmt;         // durable on the root
    mRoot->mFormatLoaded = true;
    if (mFormatLabel)             // refresh the caption in place (rebuild-on-pop also covers this)
        mFormatLabel->setText("  Format: " + formatDisplay());
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "backup.set_format",
        [fmt](MadJson::Writer& writer) {
            writer.Key("format");
            writer.String(fmt.c_str(), static_cast<rapidjson::SizeType>(fmt.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired() || ok)
                return;
            footer()->flash("Couldn't save the backup format: " +
                                MadJson::getString(payload, "message", "error"),
                            4000, true);
        });
}

void GuiMadPageBackup::fetchFormat()
{
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest("backup.get_format", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired() || !ok)
                        return;
                    const std::string was {mRoot->mFormat};
                    mRoot->mFormat = MadJson::getString(payload, "format", "gzip");
                    mRoot->mFormatLoaded = true;
                    if (mRoot->mFormat != was)
                        rebuild(); // re-render the format label in its loaded state
                });
}

void GuiMadPageBackup::fetchDest()
{
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest("backup.get_dest", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired() || !ok)
                        return;
                    mRoot->mBackupDest = MadJson::getString(payload, "dest");
                    if (mDestLabel != nullptr)
                        mDestLabel->setText("  Saving to: " + destDisplay());
                });
}

void GuiMadPageBackup::openDestPicker()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadFolderPicker([this, alive](const std::string& path) {
        if (alive.expired() || path.empty())
            return; // cancelled, or the page went away while the picker was open
        // Validate + persist FIRST, and only commit the destination (and the "Saving to:" label)
        // once the engine accepts it - so a rejected pick (unwritable mount, an in-tree folder)
        // can never become the live target or leave the label lying. mBackupDest thus always holds
        // a value the engine validated, so both backup buttons keep working.
        pageRequest(
            "backup.set_dest",
            [path](MadJson::Writer& writer) {
                writer.Key("dest");
                writer.String(path.c_str(), static_cast<rapidjson::SizeType>(path.length()));
            },
            [this, alive](bool ok, const rapidjson::Value& payload) {
                if (alive.expired())
                    return;
                if (!ok) {
                    footer()->flash("Couldn't use that folder: " +
                                        MadJson::getString(payload, "message", "error"),
                                    6000, true);
                    return;
                }
                mRoot->mBackupDest = MadJson::getString(payload, "dest");
                if (mDestLabel != nullptr)
                    mDestLabel->setText("  Saving to: " + destDisplay());
            });
    }));
}
