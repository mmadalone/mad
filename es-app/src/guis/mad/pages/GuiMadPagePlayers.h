//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePlayers.h
//
//  MAD control panel: Players section (deck-patches) — pin a pad to a player.
//  Root page edits the global [pins] (MadPlayerSlots) and lists the
//  per-system overrides; a picker page adds new per-system scopes and a
//  detail page edits [systems.<scope>.pins] with the same flows.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PLAYERS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PLAYERS_H

#include "components/ButtonComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadPlayerSlots.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <map>
#include <string>
#include <vector>

// Shared wiring for a MadPlayerSlots editor bound to one pin scope
// ("" = global [pins], else [systems.<scope>.pins]): identify via the capture
// modal, save via policy.set_pins, re-describe on devices.changed without
// losing unsaved edits.
class MadPinEditorBase : public MadPage
{
public:
    void onDevicesChanged(const rapidjson::Value& data) override;

protected:
    MadPinEditorBase(GuiMadPanel* panel, const std::string& title, const std::string& scope);

    // Creates mSlots (unpositioned) and hooks up the three callbacks; the
    // slots become a child of `parent` (nullptr = the page itself).
    void createSlots(GuiComponent* parent = nullptr);
    void requestDevices();
    // Applies the scope's pins from a merged policy object to mSlots.
    void applyPinsFromMerged(const rapidjson::Value& merged);
    void identifyPlayer(const int player);
    void savePins(const std::map<int, std::string>& pins);
    // Root refreshes its overrides grid from the post-save merged truth.
    virtual void onSaved(const rapidjson::Value& merged) {}

    std::shared_ptr<MadPlayerSlots> mSlots;
    std::string mScope;
};

class GuiMadPagePlayers : public MadPinEditorBase
{
public:
    GuiMadPagePlayers(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

protected:
    void onSaved(const rapidjson::Value& merged) override;

private:
    enum FocusTarget {
        FocusSlots = 0,
        FocusAdd = 1,
        FocusGrid = 2
    };

    void buildLayout(const rapidjson::Value& merged);
    void rebuildOverridesGrid(const rapidjson::Value& merged);
    void setFocusTarget(const int target);
    // setFocusTarget + scroll-follow: input-driven moves only (restore paths
    // must set cursor state BEFORE following).
    void moveFocus(const int target);
    // Scroll the view so the focused control (slots/grid: the focused row) is
    // visible.
    void followFocus();
    std::vector<PagedTarget> pagedTargets() const;
    void applyPagedTarget(const PagedTarget& target);

    // The whole content column lives inside mScroll (Tk _scroll parity);
    // children are positioned in view-local coordinates. The slots and the
    // grid hold their FULL height — the page scrolls, they don't.
    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<TextComponent> mPinTypes;
    std::shared_ptr<TextComponent> mGlobalHeader;
    std::shared_ptr<TextComponent> mOverridesHeader;
    std::shared_ptr<TextComponent> mNoOverrides;
    std::shared_ptr<ButtonComponent> mAddButton;
    std::shared_ptr<MadTileGrid> mGrid;

    std::map<std::string, std::string> mSystemArt; // systems.list: name → art.
    // Per-system override set: (system, "P1,2" summary), kept sorted.
    std::vector<std::pair<std::string, std::string>> mOverrideEntries;
    int mFocusTarget;
    int mGridCookie;
    float mGridTop;
    float mScrollCookie;
    bool mBuilt;
};

// Picker for a new per-system pin scope (routable systems from systems.list).
class GuiMadPagePlayersPicker : public MadPage
{
public:
    GuiMadPagePlayersPicker(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
};

class GuiMadPagePlayersDetail : public MadPinEditorBase
{
public:
    GuiMadPagePlayersDetail(GuiMadPanel* panel, const std::string& system);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    std::shared_ptr<TextComponent> mIntro;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PLAYERS_H
