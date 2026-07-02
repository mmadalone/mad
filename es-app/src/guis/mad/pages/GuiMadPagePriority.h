//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePriority.h
//
//  MAD control panel: Priority section (deck-patches) — preferred controller
//  family per system/collection (top = Player 1). GuiMadPagePriority is now
//  reached ONLY as the "PER-SYSTEM & COLLECTION RULES" subpage pushed from
//  GuiMadPageRAControllers: it lists EVERY present system + collection (via
//  racontrollers.scopes), a tile pushes the editor. GuiMadPagePriorityPicker
//  is unused since there's nothing left to "add" (kept in place, not wired).
//  The editor reorders families with carry-mode rows (A lifts, up/down move,
//  A drops, B cancels); a collection also carries a local lightgun toggle
//  saved with the order, and a system with an X-Arcade warn category gets an
//  immediate-write warn toggle (policy.set_scope_flag, optimistic + rollback).
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

class GuiMadPagePriorityPicker : public MadPage
{
public:
    GuiMadPagePriorityPicker(GuiMadPanel* panel, const std::string& kind);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    // (Re)derives the available list — build and every child pop (an editor
    // SAVE makes its entry unavailable; the Tk picker re-rendered on back).
    void refreshList();

    std::string mKind; // "system" | "collection"
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
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
        FocusClear = 3
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
    std::string mWarnKey;   // System only: the warn flag key (empty = no chip).
    std::string mWarnLabel; // System only: human label, for toasts.

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mHint;
    std::shared_ptr<MadChipRow> mScopeChip; // null when this scope has no toggle.
    std::shared_ptr<TextComponent> mLightgunNote; // Collection only.
    std::shared_ptr<MadReorderList> mList;
    std::shared_ptr<ButtonComponent> mSaveButton;
    std::shared_ptr<ButtonComponent> mClearButton;

    int mFocusTarget;
    bool mBuilt;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_PRIORITY_H
