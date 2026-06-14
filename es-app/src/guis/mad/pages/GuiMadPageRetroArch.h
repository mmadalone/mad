//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArch.h
//
//  MAD control panel: global RetroArch settings (retroarch.cfg) — configure RA
//  without dropping to desktop mode (deck-patches). The backend (retroarch.* RPCs)
//  owns the curated key set + the byte-preserving atomic write; this page renders
//  the grouped settings as MadLightgunPageBase chips/steppers and live-saves each
//  change. Per-system RA overrides stay on the Systems page.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <functional>
#include <string>

class GuiMadPageRetroArch : public MadLightgunPageBase
{
public:
    GuiMadPageRetroArch(GuiMadPanel* panel);

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

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETROARCH_H
