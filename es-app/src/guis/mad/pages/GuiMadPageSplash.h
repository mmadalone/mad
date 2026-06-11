//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSplash.h
//
//  MAD control panel: Splash section (deck-patches). Drives the [esde_splash]
//  config (mode / fit / fixed image / random pool) through splash.* methods;
//  esde-splash-gen.sh consumes the config at ES-DE startup.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SPLASH_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SPLASH_H

#include "components/ComponentList.h"
#include "guis/mad/MadPage.h"

#include <set>

class GuiMadPageSplash : public MadPage
{
public:
    GuiMadPageSplash(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    struct Option {
        std::string value;
        std::string label;
    };

    void applyConfig(const rapidjson::Value& splash);
    void rebuildList(const int cursorTo);
    void addCycleRow(const std::string& label,
                     const std::string& key,
                     const std::vector<Option>& options,
                     const std::string& current);
    // Reads the live value (mMode/mFit) — never a snapshot captured at row
    // build time, which would collapse rapid presses into duplicate writes.
    void cycleOption(const std::string& key,
                     const std::vector<Option>& options,
                     const int direction);
    void setSplash(const std::string& key, const std::string& value, const int cursorTo);
    // splash.set {key:"images", value:[]} — empty list = use-all semantics.
    void clearSavedPool();
    std::string optionLabel(const std::vector<Option>& options, const std::string& value) const;

    std::vector<Option> mModes;
    std::vector<Option> mFits;
    std::vector<std::string> mImages;
    std::set<std::string> mPool;
    std::string mMode;
    std::string mFit;
    std::string mImage;
    int mPickerCap;

    std::shared_ptr<TextComponent> mCaption;
    std::shared_ptr<ComponentList> mList;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SPLASH_H
