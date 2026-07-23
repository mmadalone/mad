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
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (server picker)

#include <cstdio>
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
         {"roms", "ROMs", false},
         {"media", "Downloaded media", false}},
    };
} // namespace

GuiMadPageBackup::GuiMadPageBackup(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "BACKUP / RESTORE"}
    , mSizesDone {false}
    , mRunning {false}
{
    for (const auto& row : CATEGORY_ROWS) {
        for (const Category& category : row)
            mInclude[category.key] = category.defaultOn;
    }
}

GuiMadPageBackup::~GuiMadPageBackup()
{
    // Detach only — the sizes stream finishes and fills the daemon-side cache,
    // and a running full backup keeps going (leaving the page must not kill a
    // half-written archive; closing the whole panel does, and the page says so).
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

void GuiMadPageBackup::build()
{
    rebuild();

    // Per-category sizes stream in as deck-backup.sh --sizes computes them
    // (du over big trees — the daemon caches them for this panel session).
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
    fetchCloud();
}

void GuiMadPageBackup::rebuild()
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    beginColumn();
    mChipRows.clear();

    header("Full backup");
    caption("Archive your whole setup — toggle what to include, then run. Writes to "
            "~/deck-config-backups. Keep MAD open until it finishes.");
    for (const auto& row : CATEGORY_ROWS) {
        std::vector<MadChipRow::Chip> chips;
        for (const Category& category : row)
            chips.push_back({category.key, chipLabel(category.key),
                             mInclude.at(category.key)});
        auto chipRow = addChips(chips, false);
        chipRow->setOnToggle([this](const std::string& key, const bool on) {
            mInclude[key] = on;
            updateTally();
        });
        mChipRows.emplace_back(chipRow);
    }
    // Placeholder text BEFORE the height is measured — an empty block
    // autosizes to ~0 and the button below would overlap the tally.
    mTally = addBlock("  Total selected: …", FONT_SIZE_SMALL, MadTheme::color(MadColor::Title),
                      smallHeight * 0.3f);
    updateTally();
    addButton("RUN FULL BACKUP NOW", [this] { runFull(); });

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
    addButton("BACK UP MAD CODE  (launchers/ → ~/deck-config-backups)", [this] {
        if (busyGuard())
            return;
        footer()->setStatus("Backing up MAD code…");
        pageRequest("backup.mad_code", nullptr, resultFlash(), 120000);
    });

    buildCloudSection();
    endColumn();
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

    // Two sliding-switch toggles (non-momentary chip row). These are harmless
    // local state, so they work whether or not S4 is reachable.
    std::vector<MadChipRow::Chip> chips {
        {"onexit", "Back up saves on exit", mCloudOnExit},
        {"timer", "Keep syncing during play", mCloudTimer}};
    mCloudToggleRow = addChips(chips, false);
    mCloudToggleRow->setOnToggle(
        [this](const std::string& which, const bool on) { setCloudToggle(which, on); });

    addButtonRow(
        {{"BACK UP NOW",
          [this] {
              if (cloudGuard())
                  return;
              cloudStream("cloud.push", "Backing up your saves to MEGA…",
                          "Saves backed up to MEGA.");
          }},
         {"SYNC LIBRARY NOW",
          [this] {
              if (cloudGuard())
                  return;
              confirmThen("Sync the big library (ROMs + media) to MEGA now? This is a large, "
                          "one-off upload — best done plugged in.",
                          [this] {
                              cloudStream("cloud.sync", "Syncing your library to MEGA…",
                                          "Library synced to MEGA.");
                          });
          }},
         {"RESTORE SAVES…", [this] {
              if (cloudGuard())
                  return;
              confirmThen("Download the latest save backup into a review folder "
                          "(~/deck-cloud-restore)? Your live files are NOT touched.",
                          [this] {
                              cloudStream("cloud.restore_precious", "Restoring saves from MEGA…",
                                          "Downloaded to ~/deck-cloud-restore (review, then copy back).");
                          });
          }}});
}

void GuiMadPageBackup::fetchCloud()
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
                        mCloudLastBackup = MadJson::getString(payload, "last_backup", "");
                    }
                    deferRelayout([this] { rebuild(); });
                },
                30000);
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

void GuiMadPageBackup::cloudStream(const std::string& method, const std::string& startStatus,
                                   const std::string& okMsg)
{
    if (mRunning) {
        footer()->flash("Another job is already running.", 3000, true);
        return;
    }
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        method, nullptr,
        [this, alive, startStatus, okMsg](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->setStatus("");
                footer()->flash("Couldn't start: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                5000, true);
                return;
            }
            mRunning = true;
            mRunToken = MadJson::getString(payload, "stream");
            footer()->setStatus(startStatus);
            backend()->setStreamCallback(
                mRunToken, [this, alive, okMsg](const rapidjson::Value& data) {
                    if (alive.expired())
                        return;
                    if (MadJson::getBool(data, "closed")) {
                        if (mRunning) {
                            mRunning = false;
                            footer()->setStatus("");
                            footer()->flash("The job ended unexpectedly.", 5000, true);
                        }
                        return;
                    }
                    if (MadJson::getBool(data, "done")) {
                        mRunning = false;
                        const int rc {MadJson::getInt(data, "rc", -1)};
                        footer()->setStatus("");
                        footer()->flash(rc == 0 ? okMsg
                                                : "FAILED (exit " + std::to_string(rc) + ").",
                                        8000, rc != 0);
                        if (rc == 0)
                            fetchCloud(); // refresh the last-backup time
                        return;
                    }
                    const std::string line {MadJson::getString(data, "line")};
                    if (!line.empty())
                        footer()->setStatus(line);
                });
        },
        30000);
}

bool GuiMadPageBackup::busyGuard()
{
    // While the full backup streams, its output lines own the footer (each
    // non-empty setStatus cancels flashes) and mixing file operations into a
    // running archive job is asking for trouble — park everything else.
    if (mRunning) {
        // mRunning now covers the full backup AND the cloud push/sync/restore
        // streams, so keep this job-neutral (not "backup").
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
    for (const auto& entry : mInclude) {
        const auto it = mSizes.find(entry.first);
        if (entry.second && it != mSizes.end())
            total += it->second;
    }
    mTally->setText("  Total selected: " + human(total) +
                    (mSizesDone ? "" : "   (calculating…)"));
}

void GuiMadPageBackup::onSizePush(const rapidjson::Value& data)
{
    if (MadJson::getBool(data, "closed")) {
        // Stream died without done (spawn failure / daemon restart): stop
        // claiming "(calculating…)" forever — show what we have.
        if (!mSizesDone) {
            mSizesDone = true;
            updateTally();
        }
        return;
    }
    if (MadJson::getBool(data, "done")) {
        mSizesDone = true;
        updateTally();
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
    for (const auto& chipRow : mChipRows) {
        const float before {chipRow->contentHeight()};
        chipRow->setChipLabel(key, chipLabel(key));
        if (chipRow->contentHeight() != before)
            reflow = true;
    }
    updateTally();
    if (reflow)
        deferRelayout([this] { rebuild(); });
}

void GuiMadPageBackup::runFull()
{
    if (mRunning) {
        footer()->flash("A full backup is already running.", 3000, true);
        return;
    }
    const std::map<std::string, bool> include {mInclude};
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "backup.run_full",
        [include](MadJson::Writer& writer) {
            writer.Key("include");
            writer.StartObject();
            for (const auto& entry : include) {
                writer.Key(entry.first.c_str(),
                           static_cast<rapidjson::SizeType>(entry.first.length()));
                writer.Bool(entry.second);
            }
            writer.EndObject();
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->setStatus("");
                footer()->flash("Couldn't start: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                5000, true);
                return;
            }
            mRunning = true;
            mRunToken = MadJson::getString(payload, "stream");
            footer()->setStatus("Backing up — keep MAD open until it finishes…");
            backend()->setStreamCallback(
                mRunToken, [this, alive](const rapidjson::Value& data) {
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
                        footer()->flash(rc == 0 ? "Full backup finished — see "
                                                  "~/deck-config-backups."
                                                : "Backup FAILED (exit " +
                                                      std::to_string(rc) + ").",
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
