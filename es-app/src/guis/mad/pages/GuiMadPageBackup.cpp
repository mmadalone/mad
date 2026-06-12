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

#include <cstdio>

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
    mTally = addBlock("", FONT_SIZE_SMALL, mMenuColorTitle, smallHeight * 0.3f);
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
    endColumn();
}

bool GuiMadPageBackup::busyGuard()
{
    // While the full backup streams, its output lines own the footer (each
    // non-empty setStatus cancels flashes) and mixing file operations into a
    // running archive job is asking for trouble — park everything else.
    if (mRunning) {
        footer()->flash("Wait for the running backup to finish first.", 3000, true);
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
