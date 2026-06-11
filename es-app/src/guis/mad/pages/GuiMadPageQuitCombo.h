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

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    enum FocusTarget {
        FocusStepper = 0,
        FocusDetect = 1,
        FocusSave = 2,
        FocusAdd = 3,
        FocusGrid = 4
    };

    // quitcombo.get → rebuild(). keepUnsaved preserves the in-memory
    // mComboButtons/mComboNames/mHold (unsaved DETECT/hold-time edits) instead
    // of overwriting them from disk — used on the post-child-pop refresh.
    void refreshData(const bool keepUnsaved = false);
    void rebuild(const rapidjson::Value& result, const bool keepUnsaved);
    void clearLayout();
    void refreshComboLine();
    void setFocusTarget(const int target);
    void detectGlobal();
    void saveGlobal();
    std::string comboString() const;

    // Page data (quitcombo.get).
    std::vector<int> mComboButtons;
    std::vector<std::string> mComboNames;
    float mHold;
    std::vector<std::pair<std::string, std::string>> mOverrides; // (system, combo names).
    std::map<std::string, std::string> mSystemArt; // systems.list: name → art.

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

    int mFocusTarget;
    int mGridCookie;
    bool mBuilt;
};

// Picker for a new per-system override: picking a system immediately arms the
// combo capture, saves it, and pops back to the root page.
class GuiMadPageQuitComboPicker : public MadPage
{
public:
    GuiMadPageQuitComboPicker(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    void armCapture(const std::string& system);

    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
};

class GuiMadPageQuitComboDetail : public MadPage
{
public:
    GuiMadPageQuitComboDetail(GuiMadPanel* panel,
                              const std::string& system,
                              const std::string& comboNames,
                              const std::string& artPath);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void redetect();
    void clearOverride();
    void applyButtonFocus();

    std::string mSystem;
    std::string mComboNames;
    std::string mArtPath;

    std::shared_ptr<ImageComponent> mArt;
    std::shared_ptr<TextComponent> mComboLine;
    std::shared_ptr<ButtonComponent> mRedetectButton;
    std::shared_ptr<ButtonComponent> mClearButton;
    int mButtonFocus;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_QUIT_COMBO_H
