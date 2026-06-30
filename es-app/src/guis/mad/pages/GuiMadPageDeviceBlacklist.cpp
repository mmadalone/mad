//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageDeviceBlacklist.cpp
//
//  MAD control panel: "Device visibility" toggle list (deck-patches). See the
//  header. Each connected controller is a chip; chip ON = visible to the emulator,
//  chip OFF = hidden (blacklisted). Toggling fires pcsx2blacklist.set optimistically
//  and reverts the chip if the write fails.
//

#include "guis/mad/pages/GuiMadPageDeviceBlacklist.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

#include <vector>

GuiMadPageDeviceBlacklist::GuiMadPageDeviceBlacklist(GuiMadPanel* panel, const std::string& title,
                                                     const std::string& emu)
    : MadLightgunPageBase {panel, title}
    , mEmu {emu}
{
}

void GuiMadPageDeviceBlacklist::build()
{
    setLoadingText("Loading devices…");
    const std::string emu {mEmu};
    pageRequest(
        "pcsx2blacklist.get",
        [emu](MadJson::Writer& writer) {
            writer.Key("emu");
            writer.String(emu.c_str(), static_cast<rapidjson::SizeType>(emu.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load devices: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        },
        8000);
}

void GuiMadPageDeviceBlacklist::rebuild(const rapidjson::Value& result)
{
    beginColumn();

    const std::string note {MadJson::getString(
        result, "note", "Hidden devices are invisible to the emulator at launch.")};
    addBlock(note, FONT_SIZE_SMALL, MadTheme::color(MadColor::Primary),
             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);

    std::vector<MadChipRow::Chip> chips;
    const rapidjson::Value& devices {MadJson::getMember(result, "devices")};
    if (devices.IsArray()) {
        for (const rapidjson::Value& d : devices.GetArray()) {
            const std::string vidpid {MadJson::getString(d, "vidpid")};
            if (vidpid.empty())
                continue;
            const std::string label {MadJson::getString(d, "label", vidpid)};
            // Chip ON = VISIBLE = NOT hidden.
            chips.push_back({vidpid, label, !MadJson::getBool(d, "hidden", false)});
        }
    }

    if (chips.empty()) {
        addBlock("○  No controllers connected.", FONT_SIZE_SMALL,
                 MadTheme::color(MadColor::Secondary),
                 Font::get(FONT_SIZE_SMALL)->getHeight() * 0.2f);
        endColumn();
        return;
    }

    auto row = addChips(chips, false);
    mChipRow = row; // weak_ptr rollback guard for toggle()'s failure path.
    row->setOnToggle([this](const std::string& value, const bool on) {
        // Chip ON = visible, so the device's hidden state is the inverse.
        toggle(value, !on);
    });

    endColumn();
}

void GuiMadPageDeviceBlacklist::toggle(const std::string& vidpid, const bool hidden)
{
    const std::string emu {mEmu};
    pageRequest(
        "pcsx2blacklist.set",
        [emu, vidpid, hidden](MadJson::Writer& writer) {
            writer.Key("emu");
            writer.String(emu.c_str(), static_cast<rapidjson::SizeType>(emu.length()));
            writer.Key("vidpid");
            writer.String(vidpid.c_str(), static_cast<rapidjson::SizeType>(vidpid.length()));
            writer.Key("hidden");
            writer.Bool(hidden);
        },
        [this, vidpid, hidden](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                // The chip optimistically flipped to visible = !hidden; revert it to
                // its pre-toggle state (visible = hidden) so it matches the on-disk
                // truth. weak_ptr: harmless no-op if a rebuild() already replaced it.
                if (auto chipRow = mChipRow.lock())
                    chipRow->setChipState(vidpid, hidden);
                footer()->flash("Couldn't change visibility: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash(MadJson::getString(payload, "message", "Saved"));
        });
}
