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
#include "guis/mad/pages/GuiMadPageCloudProgress.h" // CloudProgress + the progress subpage
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
        {{"roms", "ROMs (SD)", false},
         {"romsint", "ROMs (internal)", false},
         {"openbor", "OpenBOR", false}},
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
    : MadLightgunPageBase {panel,
                           section == Section::Local ? "LOCAL BACKUP" : "CLOUD BACKUP (MEGA)"}
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

    // Cloud (MEGA): connection/toggle state + the server list, both async.
    if (mSection == Section::Cloud)
        fetchCloud();
}

void GuiMadPageBackup::rebuild()
{
    if (mSection == Section::Landing) {
        rebuildLanding();
        return;
    }
    beginColumn();
    mChipRows.clear();
    if (mSection == Section::Local)
        buildLocalSections();
    else
        buildCloudSection();
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
    MadTileGrid::Tile local;
    local.key = "local";
    local.label = "Local";
    local.artPath = MadTheme::routerIconPath("backup-local");
    tiles.emplace_back(local);

    MadTileGrid::Tile cloud;
    cloud.key = "cloud";
    cloud.label = "Cloud (MEGA)";
    cloud.artPath = MadTheme::routerIconPath("backup-cloud-mega");
    tiles.emplace_back(cloud);

    // The transfers tile is present only while a CLOUD transfer is live (a full backup reports
    // through the footer and has no progress subpage). "Transfers" stays short to avoid clipping.
    const bool transferLive {mCloudProgress != nullptr && mCloudProgress->active &&
                             !mCloudProgress->done};
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
        if (key == "local")
            mPanel->pushPage(new GuiMadPageBackup(mPanel, this, Section::Local));
        else if (key == "cloud")
            mPanel->pushPage(new GuiMadPageBackup(mPanel, this, Section::Cloud));
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
    caption("Where RUN FULL BACKUP and BACK UP MAD CODE write their archives. Pick any folder "
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

    header("Router config backup");
    caption("Snapshot / revert the emulator controller configs the router writes, plus "
            "the GUI's own overrides (controller-policy.local.toml).");
    addButtonRow(
        {{"BACKUP",
          [this] {
              if (busyGuard())
                  return;
              footer()->setStatus("Snapshotting the router configs…");
              pageRequest("backup.snapshot", nullptr, resultFlash(), 60000);
          }},
         {"RESTORE",
          [this] {
              if (busyGuard())
                  return;
              confirmThen(
                  "Restore the snapshot over the live emulator configs and the GUI "
                  "overrides? Close any open emulators first.",
                  [this] { pageRequest("backup.restore", nullptr, resultFlash(), 60000); });
          }},
         {"RESTORE INPUT BACKUPS",
          [this] {
              if (busyGuard())
                  return;
              confirmThen(
                  "Revert every emulator input config to its one-time .router-backup "
                  "(the state before MAD's first write)?",
                  [this] {
                      pageRequest("backup.restore_router", nullptr, resultFlash(), 30000);
                  });
          }},
         {"RESET OVERRIDES",
          [this] {
              if (busyGuard())
                  return;
              confirmThen(
                  "Delete ALL GUI overrides (controller-policy.local.toml) and revert "
                  "to the documented defaults?",
                  [this] { pageRequest("backup.reset_local", nullptr, resultFlash()); });
          }}});
    addButton("BACK UP MAD CODE  (launchers/)", [this] {
        if (busyGuard())
            return;
        const std::string dest {mRoot->mBackupDest};
        footer()->setStatus("Backing up MAD code…");
        pageRequest(
            "backup.mad_code",
            [dest](MadJson::Writer& writer) {
                if (!dest.empty()) {
                    writer.Key("dest");
                    writer.String(dest.c_str(),
                                  static_cast<rapidjson::SizeType>(dest.length()));
                }
            },
            resultFlash(), 120000);
    });
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
    else {
        caption("Not connected. Run the cloud setup once in Desktop Mode "
                "(deck-cloud-setup.sh). Your server choice below is still saved.");
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

    // Two sliding-switch toggles (non-momentary chip row). Harmless local state — they work
    // whether or not S4 is reachable.
    header("When to back up");
    std::vector<MadChipRow::Chip> chips {
        {"onexit", "Back up saves on exit", mCloudOnExit},
        {"timer", "Keep syncing during play", mCloudTimer},
        {"autoresume", "Auto-resume interrupted transfers", mCloudAutoResume}};
    mCloudToggleRow = addChips(chips, false);
    mCloudToggleRow->setOnToggle(
        [this](const std::string& which, const bool on) { setCloudToggle(which, on); });

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
                        mCloudOnExit = MadJson::getBool(payload, "onexit_enabled");
                        mCloudTimer = MadJson::getBool(payload, "timer_active");
                        mCloudAutoResume = MadJson::getBool(payload, "autoresume_enabled");
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

void GuiMadPageBackup::setCloudToggle(const std::string& which, const bool on)
{
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "cloud.set_toggle",
        [which, on](MadJson::Writer& writer) {
            writer.Key("which");
            writer.String(which.c_str(), static_cast<rapidjson::SizeType>(which.length()));
            writer.Key("value");
            writer.String(on ? "on" : "off");
        },
        [this, alive, which, on](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (ok) {
                if (which == "onexit")
                    mCloudOnExit = on;
                else if (which == "timer")
                    mCloudTimer = on;
                else if (which == "autoresume")
                    mCloudAutoResume = on;
                // Re-sync the switch to the saved truth: a rebuild (e.g. a du size
                // push) between the press and this response recreates the chip row
                // from the members, which only just updated — mirror the failure
                // path so the switch never shows the opposite of what was saved.
                // setChipState no-ops when already correct.
                if (mCloudToggleRow != nullptr)
                    mCloudToggleRow->setChipState(which, on);
                footer()->flash(MadJson::getString(payload, "message", "Saved."), 3000, false);
            }
            else {
                // The chip flipped optimistically on press — put it back.
                if (mCloudToggleRow != nullptr)
                    mCloudToggleRow->setChipState(which, !on);
                footer()->flash(
                    MadJson::getString(payload, "message", "Could not change the setting."), 5000,
                    true);
            }
        },
        30000);
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
    deferRelayout([this] { rebuild(); });
}

bool GuiMadPageBackup::input(InputConfig* config, Input input)
{
    if (mSection == Section::Landing)
        return mGrid != nullptr && mGrid->input(config, input);
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
            else
                footer()->flash(rc == 0 ? okMsg : "FAILED (exit " + std::to_string(rc) + ").", 8000,
                                rc != 0);
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

void GuiMadPageBackup::fetchActive()
{
    // Landing reattach: if the daemon already has a transfer running (e.g. a timer sync or a
    // crash-auto-resumed upload), adopt it so the Ongoing-transfers tile + its progress reflect it.
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "cloud.active", nullptr,
        [this, alive](bool ok, const rapidjson::Value& payload) {
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
                installRunStream(MadJson::getString(payload, "token"), "Transfer finished.",
                                 reattachRestore);
                deferRelayout([this] { rebuild(); }); // reveal the Ongoing-transfers tile
            }
            // Only offer the restore-resume prompt when nothing is already running.
            if (!running && MadJson::getBool(payload, "pending_restore"))
                promptResumeRestore();
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
                        mRunning = false;
                        const int rc {MadJson::getInt(data, "rc", -1)};
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
