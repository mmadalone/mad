//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageModel2.h
//
//  MAD control panel: Sega Model 2 emulator (ElSemi m2emu, Proton) settings
//  (deck-patches). A single global EMULATOR.INI editor — no per-game scope, m2emu
//  has one shared INI. The backend (model2.* RPCs) owns the curated key set and
//  the comment-preserving atomic write; this page just renders the grouped
//  settings as MadLightgunPageBase chips/steppers and live-saves each change.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MODEL2_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MODEL2_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <functional>
#include <string>

class GuiMadPageModel2 : public MadLightgunPageBase
{
public:
    GuiMadPageModel2(GuiMadPanel* panel);

    void build() override;
    void onChildPopped() override {} // No sub-pages; nothing to refresh.

private:
    void rebuild(const rapidjson::Value& result);
    void addEnumStepper(const rapidjson::Value& setting, const std::string& key,
                        const std::string& label, const std::string& type);
    void addNumberStepper(const rapidjson::Value& setting, const std::string& key,
                          const std::string& label, bool isFloat);
    // Live-save one setting: value is always sent as a STRING (the backend coerces
    // by the key's declared type). revert (chip rollback) runs on write failure.
    void setOption(const std::string& key, const std::string& value,
                   const std::string& label,
                   const std::function<void()>& revert = nullptr);
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MODEL2_H
