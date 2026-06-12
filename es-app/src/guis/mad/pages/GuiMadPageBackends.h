//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackends.h
//
//  MAD control panel: Backends section (deck-patches). The root page lists
//  every [backends.*] table whose system has games; the detail page renders
//  backends.describe's ORDERED typed knob list (bool / class_set / int /
//  slot_set / choice / slot_profiles) — fully schema-driven, no per-backend
//  hardcoding. Writes go through policy.set_backend_* and profiles.apply_slot.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKENDS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKENDS_H

#include "components/ButtonComponent.h"
#include "components/ComponentList.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadChipRow.h"
#include "guis/mad/widgets/MadScrollView.h"
#include "guis/mad/widgets/MadStepper.h"
#include "guis/mad/widgets/MadTileGrid.h"

#include <functional>
#include <string>
#include <utility>
#include <vector>

class GuiMadPageBackends : public MadPage
{
public:
    GuiMadPageBackends(GuiMadPanel* panel);

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

    std::shared_ptr<MadScrollView> mScroll;
    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<MadTileGrid> mGrid;
    std::shared_ptr<TextComponent> mHiddenNote;

    int mGridCookie;
    float mScrollCookie;
};

// Generic option picker: a ComponentList of (value, label) rows; choosing
// fires onChoose then pops back (the parent refreshes via onChildPopped).
// The native form of the Tk _select_page.
class GuiMadPageBackendChoice : public MadPage
{
public:
    GuiMadPageBackendChoice(GuiMadPanel* panel,
                            const std::string& title,
                            const std::string& caption,
                            const std::vector<std::pair<std::string, std::string>>& options,
                            const std::string& current,
                            const std::function<void(const std::string&)>& onChoose);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    std::string mCaption;
    std::vector<std::pair<std::string, std::string>> mOptions;
    std::string mCurrent;
    std::function<void(const std::string&)> mOnChoose;

    std::shared_ptr<TextComponent> mCaptionText;
    std::shared_ptr<ComponentList> mList;
};

class GuiMadPageBackendDetail : public MadPage
{
public:
    GuiMadPageBackendDetail(GuiMadPanel* panel, const std::string& backend);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    void onChildPopped() override;

private:
    // One focusable control in the schema-driven column.
    struct Control {
        enum class Type { Chips, Stepper, Button };
        Type type;
        GuiComponent* comp; // For onFocusGained/Lost + input routing.
        float top;          // View-local rect (focus-follow + LT/RT paging).
        float bottom;
    };

    void refresh();
    void rebuild(const rapidjson::Value& result);
    void clearLayout();
    void setFocus(const int index);
    void followFocus();
    void setBackendKey(const std::string& key, const MadJson::ParamsWriter& valueWriter,
                       const std::string& shown);
    void openChoice(const std::string& title,
                    const std::string& caption,
                    const std::vector<std::pair<std::string, std::string>>& options,
                    const std::string& current,
                    const std::function<void(const std::string&)>& onChoose);

    std::string mBackend;

    std::shared_ptr<MadScrollView> mScroll;
    // Owns every laid-out component (texts + controls); cleared on rebuild.
    std::vector<std::shared_ptr<GuiComponent>> mWidgets;
    std::vector<Control> mControls;

    int mFocus;
    int mFocusCookie;
    float mScrollCookie;
    bool mBuilt;
    // A profiles.apply_slot is in flight: skip the pop-triggered refresh —
    // the apply's own response refreshes once, with post-apply truth (the
    // pop refresh would render the pre-apply state for a frame and waste a
    // round-trip).
    bool mSuppressChildPopRefresh;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKENDS_H
