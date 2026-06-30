//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageDeviceBlacklist.h
//
//  MAD control panel: "Device visibility" toggle list (deck-patches). Opened from
//  the Standalones -> PS2 (pcsx2) tile. Lists every connected controller as a
//  toggle chip: chip ON = VISIBLE to the emulator, chip OFF = HIDDEN (blacklisted
//  from the emulator at launch so the real pads number correctly). Backed by the
//  pcsx2blacklist.get / pcsx2blacklist.set RPCs; each toggle saves immediately and
//  rolls the chip back on a write failure.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_DEVICE_BLACKLIST_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_DEVICE_BLACKLIST_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.
#include "guis/mad/widgets/MadChipRow.h"

#include <memory>
#include <string>

class GuiMadPageDeviceBlacklist : public MadLightgunPageBase
{
public:
    // emu = the emulator key sent on every get/set (e.g. "pcsx2"). title shows in
    // the header.
    GuiMadPageDeviceBlacklist(GuiMadPanel* panel, const std::string& title,
                              const std::string& emu);

    void build() override;
    void onChildPopped() override {}

private:
    void rebuild(const rapidjson::Value& result);
    // hidden = the NEW blacklist state for this device (true = hide it from the
    // emulator). On a write failure the chip is rolled back to its pre-toggle state.
    void toggle(const std::string& vidpid, bool hidden);

    std::string mEmu;
    // The live chip row, for rollback after a failed pcsx2blacklist.set: a weak_ptr
    // so it harmlessly expires if a rebuild() destroyed the row before the in-flight
    // reply lands (same guard as GuiMadPageBackends' class_set knob).
    std::weak_ptr<MadChipRow> mChipRow;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_DEVICE_BLACKLIST_H
