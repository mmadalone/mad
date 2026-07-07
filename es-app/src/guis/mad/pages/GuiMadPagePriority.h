//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePriority.h
//
//  MAD control panel: the RetroArch hub "Per-system settings" page (deck-patches)
//  -- preferred controller family per system/collection (top = Player 1).
//  GuiMadPagePriority is the two-grid list (systems + collections) pushed from
//  GuiMadPageStandaloneSections via the "priority_scopes" section kind; a tile
//  pushes the editor. (The old GuiMadPagePriorityPicker "add" page was removed in
//  the RA-hub Phase 4 cleanup.) The editor reorders families with carry-mode rows
//  (A lifts, up/down move, A drops, B cancels); a collection also carries a local
//  lightgun toggle saved with the order; a system gets immediate-write X-Arcade
//  warn + Hands-off chips (policy.set_scope_flag) and, when it has RA cores, a
//  "RetroArch options" button opening its per-system rasys_<system> settings.
//  Writes via policy.set_ports / policy.clear_ports (the order).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PRIORITY_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PRIORITY_H

#include "components/ButtonComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadChipRow.h"
#include "guis/mad/widgets/MadReorderList.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <map>
#include <string>
#include <vector>

class GuiMadPagePriority : public MadPage
{
public:
    GuiMadPagePriority(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    enum FocusTarget {
        FocusSystemGrid = 0,
        FocusCollectionGrid = 1
    };

    void rebuild(const rapidjson::Value& result);
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    // The next/previous EXISTING focus target from `target` (a grid may be
    // absent when nothing is present for that kind).
    int nextTarget(int target, const int direction) const;

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<TextComponent> mSystemsHeader;
    std::shared_ptr<TextComponent> mNoSystems;
    std::shared_ptr<MadTileGrid> mSystemGrid;
    std::shared_ptr<TextComponent> mCollectionsHeader;
    std::shared_ptr<TextComponent> mNoCollections;
    std::shared_ptr<MadTileGrid> mCollectionGrid;

    int mFocusTarget;
    int mSystemGridCookie;
    int mCollectionGridCookie;
    float mScrollCookie;
    bool mBuilt;
};

class GuiMadPagePriorityEdit : public MadPage
{
public:
    // `displayTitle`, when non-empty, is used as the page header verbatim
    // instead of the default "PRIORITY: " + toUpper(name) — the per-game
    // Controllers page (GuiMadPageStandaloneSections' pergame_priority
    // branch) passes its own clean "<Game Name> — Controllers" title so the
    // header doesn't show a raw "<system>:<stem>" titleid uppercased.
    GuiMadPagePriorityEdit(GuiMadPanel* panel, const std::string& name,
                           const std::string& kind,
                           const std::string& displayTitle = "");

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    bool onBackPressed() override; // B cancels a reorder carry first.
    std::vector<HelpPrompt> getHelpPrompts() override;
    // Buffered X=Save / Y=Cancel: the order AND the collection lightgun chip
    // stage in the frontend; dirty = either differs from the load baseline. The
    // system warn chip is an immediate write and is NOT part of this buffer.
    bool madSave() override;
    bool madCancel() override;
    bool hasUnsavedEdits() const override;

private:
    // FocusChip is the ONE scope toggle slot, whichever it is for this scope:
    // collection -> the lightgun chip (local, saved with Save); system -> an
    // immediate-write warn chip, present only when priority.get returned a
    // "warn" object. Absent for a system with no warn category (exactly like
    // a collection-less system had no lightgun slot).
    enum FocusTarget {
        FocusChip = 0,
        FocusList = 1,
        FocusSave = 2,
        FocusClear = 3,
        FocusRaOptions = 4
    };

    void rebuild(const rapidjson::Value& result);
    void setFocusTarget(const int target);
    void moveFocus(const int target);
    void followFocus();
    void save();
    void clearRule();
    // System scope only: policy.set_scope_flag, optimistic apply with
    // rollback-on-failure (ported from the old global root's setScopeFlag()).
    void setWarnFlag(const std::string& flag, bool value);

    std::string mName;  // RPC payload id ("<system>:<stem>" for game scope)
    std::string mLabel; // human-facing name for footer flashes
    std::string mKind;
    int mNports;
    bool mLightgun; // Collection only: saved with Save, like the Tk BooleanVar.
    // System scope: label per immediate-write toggle key (X-Arcade warn +
    // Hands-off/router_skip), for toasts + write-failure rollback. Empty = no chip.
    std::map<std::string, std::string> mToggleLabels;
    // Buffered-editor baseline captured at load: dirty = order or (collection) lightgun changed.
    std::vector<std::string> mBaselineOrder;
    bool mBaselineLightgun {false};

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mHint;
    std::shared_ptr<MadChipRow> mScopeChip; // null when this scope has no toggle.
    std::shared_ptr<TextComponent> mLightgunNote; // Collection only.
    std::shared_ptr<MadReorderList> mList;
    std::shared_ptr<ButtonComponent> mSaveButton;
    std::shared_ptr<ButtonComponent> mClearButton;
    // System scope with RA cores: opens the per-system RetroArch options
    // (rasys_<system>) via GuiMadPageEmuSettings.
    std::shared_ptr<ButtonComponent> mRaOptionsButton;

    int mFocusTarget;
    bool mBuilt;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PRIORITY_H
