//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageQuitCombo.h
//
//  MAD control panel: Quit-game combo section (deck-patches). Global combo
//  (buttons + hold time) plus per-system overrides; combos are captured with
//  the press-a-combo modal and written through policy.set_quit_combo.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_QUIT_COMBO_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_QUIT_COMBO_H

#include "components/ButtonComponent.h"
#include "components/ImageComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadStepper.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <map>
#include <string>
#include <vector>

class GuiMadPageQuitCombo : public MadPage
{
public:
    GuiMadPageQuitCombo(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    // Buffered X=Save / Y=Cancel for the GLOBAL combo (the in-memory
    // mComboButtons/mHold are the buffer); dirty = they differ from the
    // last-saved baseline. Per-system overrides save immediately (their
    // sub-pages are not buffered).
    bool madSave() override;
    bool madCancel() override;
    bool hasUnsavedEdits() const override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    enum FocusTarget {
        FocusStepper = 0,
        FocusDetect = 1,
        FocusSave = 2,
        FocusAdd = 3,
        FocusGrid = 4,
        FocusAddColl = 5,  // ADD PER-COLLECTION COMBO button
        FocusGridColl = 6  // per-collection overrides grid
    };

    // quitcombo.get → rebuild(). keepUnsaved preserves the in-memory
    // mComboButtons/mComboNames/mHold (unsaved DETECT/hold-time edits) instead
    // of overwriting them from disk — used on the post-child-pop refresh.
    void refreshData(const bool keepUnsaved = false);
    void rebuild(const rapidjson::Value& result, const bool keepUnsaved);
    void clearLayout();
    void refreshComboLine();
    void setFocusTarget(const int target);
    // setFocusTarget + scroll-follow: input-driven moves only (rebuild and
    // onRestoreFocus must restore cursor state BEFORE following).
    void moveFocus(const int target);
    // Scroll the view so the focused control (grid: the cursor row) is visible.
    void followFocus();
    std::vector<PagedTarget> pagedTargets() const;
    void applyPagedTarget(const PagedTarget& target);
    void detectGlobal();
    void saveGlobal();
    std::string comboString() const;

    // Page data (quitcombo.get).
    std::vector<int> mComboButtons;
    std::vector<std::string> mComboNames;
    float mHold;
    // Last-saved baseline for the buffered global combo (names track buttons, so
    // the baseline only needs {buttons, hold}). Captured on a non-keepUnsaved load.
    std::vector<int> mBaselineButtons;
    float mBaselineHold {1.0f};
    std::vector<std::pair<std::string, std::string>> mOverrides; // (system, combo names).
    std::map<std::string, std::string> mSystemArt; // systems.list: name → art.
    // Per-collection overrides: (collection display name, combo names). A collection
    // combo overrides the system/per-game combo; its scope key is "collection-<name>".
    std::vector<std::pair<std::string, std::string>> mCollOverrides;
    std::map<std::string, std::string> mCollArt; // collections.list: name → theme console art.

    // The whole content column lives inside mScroll (Tk _scroll parity);
    // children are positioned in view-local coordinates.
    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<TextComponent> mGlobalHeader;
    std::shared_ptr<TextComponent> mComboLine;
    std::shared_ptr<MadStepper> mStepper;
    std::shared_ptr<ButtonComponent> mDetectButton;
    std::shared_ptr<ButtonComponent> mSaveButton;
    std::shared_ptr<TextComponent> mPerSystemHeader;
    std::shared_ptr<TextComponent> mWiiNote;
    std::shared_ptr<ButtonComponent> mAddButton;
    std::shared_ptr<TextComponent> mNoOverrides;
    std::shared_ptr<MadTileGrid> mGrid;
    std::shared_ptr<TextComponent> mPerCollHeader;
    std::shared_ptr<ButtonComponent> mAddCollButton;
    std::shared_ptr<TextComponent> mNoCollOverrides;
    std::shared_ptr<MadTileGrid> mCollGrid;

    int mFocusTarget;
    int mGridCookie;
    int mCollGridCookie;
    float mScrollCookie;
    bool mBuilt;
};

// Picker for a new per-system OR per-collection override: picking a tile immediately
// arms the combo capture, saves it, and pops back to the root page. collections=true
// lists custom collections (scope "collection-<name>") instead of systems.
class GuiMadPageQuitComboPicker : public MadPage
{
public:
    GuiMadPageQuitComboPicker(GuiMadPanel* panel, bool collections = false);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    // `label` is the display name (system or collection); the write scope is `label`
    // for systems and "collection-<label>" for collections.
    void armCapture(const std::string& label);

    bool mCollections;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
};

class GuiMadPageQuitComboDetail : public MadPage
{
public:
    // `system` is the DISPLAY name (shown in the title/messages). `scopeKey` is the
    // policy scope written by set/clear_quit_combo — empty means it equals `system`
    // (per-system); for a collection pass "collection-<name>" so the display stays the
    // bare collection name while the stored key matches the watcher hook.
    GuiMadPageQuitComboDetail(GuiMadPanel* panel,
                              const std::string& system,
                              const std::string& comboNames,
                              const std::string& artPath,
                              const std::string& scopeKey = "");

    void build() override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void redetect();
    void clearOverride();
    void applyButtonFocus();

    std::string mSystem;
    std::string mScopeKey;
    std::string mComboNames;
    std::string mArtPath;

    std::shared_ptr<ImageComponent> mArt;
    std::shared_ptr<TextComponent> mComboLine;
    std::shared_ptr<ButtonComponent> mRedetectButton;
    std::shared_ptr<ButtonComponent> mClearButton;
    int mButtonFocus;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_QUIT_COMBO_H
