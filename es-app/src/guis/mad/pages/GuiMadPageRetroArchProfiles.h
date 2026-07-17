//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchProfiles.h
//
//  MAD control panel: RetroArch input PROFILES (deck-patches, P3). A tiled list of
//  named hotkey profiles (raprof.list) with a "+ New profile" tile (raprof.create
//  via the on-screen keyboard). Picking a profile opens the generic buffered
//  GuiMadPageEmuSettings under ns "raprof" (families as switches, the 6 hotkey rows
//  as token pickers, plus a Delete / Reset-to-shipped action) -- so the per-profile
//  editor is ZERO new C++, this root list is the only page. Writes land in
//  controller-policy.local.toml; the shipped base profiles are read-only (editable
//  as a local shadow, never deletable). See lib/madsrv/ra_profiles_cmds.py.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_PROFILES_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_PROFILES_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <memory>
#include <string>

class GuiMadPageRetroArchProfiles : public MadPage
{
public:
    GuiMadPageRetroArchProfiles(GuiMadPanel* panel, const std::string& title);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    void rebuild(const rapidjson::Value& result);
    void followFocus();
    void openProfile(const std::string& name);
    void promptCreate();

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};
    float mScrollCookie {0.0f};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_RETRO_ARCH_PROFILES_H
