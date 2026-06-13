//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSystems.h
//
//  MAD control panel: Systems section (deck-patches). A console-art tile grid
//  of gamelist-backed systems; picking one pushes a detail page with the
//  per-system policy toggles.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEMS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEMS_H

#include "components/ComponentList.h"
#include "components/SwitchComponent.h"
#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadTileGrid.h"

class GuiMadPageSystems : public MadPage
{
public:
    GuiMadPageSystems(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;
    // A detail-page toggle may have changed the ● badge/sublabel truth.
    void onChildPopped() override;

private:
    void requestSystems();

    std::shared_ptr<MadTileGrid> mGrid;
};

class GuiMadPageSystemDetail : public MadPage
{
public:
    GuiMadPageSystemDetail(GuiMadPanel* panel, const std::string& system);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

    void onSaveFocus() override;
    void onRestoreFocus() override;

private:
    struct ToggleRow {
        std::string flag;
        std::shared_ptr<SwitchComponent> switchComp;
    };
    // RetroArch per-system option toggles (keyed by option id, not policy flag).
    struct RaToggleRow {
        std::string id;
        std::shared_ptr<SwitchComponent> switchComp;
    };

    void populate(const rapidjson::Value& result);
    void setFlag(const std::string& flag, const bool value);
    void setRaOption(const std::string& id, const bool value);

    std::string mSystem;
    std::shared_ptr<ImageComponent> mArt;
    std::shared_ptr<TextComponent> mBackendLine;
    std::shared_ptr<TextComponent> mManagedLine;
    std::shared_ptr<ComponentList> mList;
    std::vector<ToggleRow> mToggles;
    std::vector<RaToggleRow> mRaToggles;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEMS_H
