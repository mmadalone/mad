//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBezelAssign.h
//
//  MAD control panel: assign / reassign an existing bezel to a same-system game
//  (deck-patches). Use case: a community-patched ROM ("… (English)") whose name
//  doesn't 1:1-match any Bezel-Project bezel gets none — point it at an existing
//  same-system bezel. Two searchable pickers: a TARGET game (bezels.roms) then a
//  SOURCE bezel (bezels.available, with preview). Backend: bezels.assign.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_ASSIGN_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_ASSIGN_H

#include "components/ButtonComponent.h"
#include "components/ImageComponent.h"
#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scroll-list scaffolding.

#include <functional>
#include <string>
#include <vector>

// Target-game picker: every ROM of the system (bezels.roms), each showing the bezel
// it currently points at; pick one to choose a source bezel for it. Y filters.
class GuiMadPageBezelAssign : public MadLightgunPageBase
{
public:
    GuiMadPageBezelAssign(GuiMadPanel* panel, const std::string& key, const std::string& label,
                          const std::function<void()>& onChanged = nullptr);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void onChildPopped() override; // an assignment happened in the source picker — refresh
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Rom {
        std::string game;          // rom stem — the assign TARGET write key
        std::string assigned;      // bezel-stem it currently points at, or "" if none
        bool hasOwn;
        std::string title;         // gamelist <name>; "" -> fall back to the stem
        std::string assignedTitle; // title of the assigned bezel; "" -> the bezel stem
    };
    static std::string rowText(const Rom& r) { return r.title.empty() ? r.game : r.title; }
    void populate();
    void openSearch();
    void pickSource(int i); // open the source-bezel picker for shown game i

    std::string mKey;
    std::string mLabel;
    std::string mFilter;
    std::vector<Rom> mRoms;
    std::vector<Rom> mShown; // filtered subset, parallel to mButtons
    std::vector<std::shared_ptr<ButtonComponent>> mButtons;
    std::function<void()> mOnChanged; // notify the Bezel detail page (game count changed)
    bool mDirty {false};              // a child assignment happened — refresh on return
};

// Source-bezel picker (with preview): every installed bezel for the system
// (bezels.available); pick one to assign to the target game (bezels.assign). Y filters.
class GuiMadPageBezelSource : public MadLightgunPageBase
{
public:
    GuiMadPageBezelSource(GuiMadPanel* panel, const std::string& key, const std::string& target,
                          const std::function<void()>& onAssigned = nullptr);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void onChildPopped() override {}
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Bezel {
        std::string name;    // bezel stem — the assign SOURCE write key
        std::string preview;
        std::string title;   // gamelist <name> for the bezel's game; "" -> the stem
    };
    static std::string rowText(const Bezel& b) { return b.title.empty() ? b.name : b.title; }
    void populate();
    void updatePreview(); // show the focused bezel
    void openSearch();
    void assign(int i); // assign shown bezel i to the target game

    std::string mKey;
    std::string mTarget;
    std::string mFilter;
    std::vector<Bezel> mBezels;
    std::vector<Bezel> mShown; // filtered subset, parallel to mButtons
    std::vector<std::shared_ptr<ButtonComponent>> mButtons;
    std::shared_ptr<ImageComponent> mPreview;
    std::function<void()> mOnAssigned; // notify the target picker an assignment happened
    bool mAssignInFlight {false};      // guard against double-assign while a write is pending
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BEZEL_ASSIGN_H
