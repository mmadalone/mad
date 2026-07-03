//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmuSettings.h
//
//  MAD control panel: GENERIC GROUPS-driven settings page (deck-patches). Reused
//  by every standalone emulator's Settings section — parameterised only by the
//  backend RPC namespace (e.g. "dolphin" -> dolphin.get / dolphin.set) and the
//  page title. The backend owns the curated key set + the safe atomic write; this
//  page renders bool/int/float/enum/resolution controls and live-saves each change.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_SETTINGS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_SETTINGS_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <functional>
#include <string>

class GuiMadPageEmuSettings : public MadLightgunPageBase
{
public:
    // ns = RPC namespace; get/set are "<ns>.get" / "<ns>.set". title shows in the
    // header. ctxKey/ctxVal (optional) = an extra param sent on every get/set —
    // e.g. ("titleid","0100…") to target a per-game config via the same backend.
    // core (optional, RetroArch per-game only) = an extra "core" param sent on every
    // get/set/save/cancel: when set, the backend reads/writes ONLY that core; when
    // empty, it keeps reading the launched core and writing all cores ("All cores").
    GuiMadPageEmuSettings(GuiMadPanel* panel, const std::string& title, const std::string& ns,
                          const std::string& ctxKey = "", const std::string& ctxVal = "",
                          const std::string& core = "");

    void build() override;
    void onChildPopped() override {}
    bool madSave() override;
    bool madCancel() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void rebuild(const rapidjson::Value& result);
    void addEnumStepper(const rapidjson::Value& setting, const std::string& key,
                        const std::string& label, const std::string& type);
    void addNumberStepper(const rapidjson::Value& setting, const std::string& key,
                          const std::string& label, bool isFloat);
    // A focusable button whose press fires an arbitrary RPC (the item's "rpc" +
    // optional "args" object of string values), flashing the result message —
    // e.g. pcsx2x6's "Start Sinden guns". Never routes through <ns>.set.
    void addActionButton(const rapidjson::Value& setting, const std::string& label);
    void setOption(const std::string& key, const std::string& value, const std::string& label,
                   const std::function<void()>& revert = nullptr);
    // Buffered mode (backend sends "buffered":true, e.g. Lindbergh): each set stages
    // into a backend buffer instead of writing; SAVE commits, CANCEL reverts to disk.
    void requestSave();
    void requestCancel();

    std::string mNs;
    std::string mCtxKey; // extra request param (e.g. "titleid"); empty = none
    std::string mCtxVal;
    std::string mCore; // RetroArch: extra "core" request param; empty = all cores
    bool mBuffered {false}; // true once a .get/.cancel payload reports buffered Save/Cancel
    bool mDirty {false}; // true once a buffered change is staged, unsaved
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_SETTINGS_H
