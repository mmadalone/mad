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

#include <functional>

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

    std::shared_ptr<TextComponent> mIntro; // clarifies what this page governs
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
    // One toggle row, used for both kinds: `key` is the policy flag OR the
    // RetroArch option id (the two stay in separate vectors, same shape).
    struct ToggleRow {
        std::string key;
        std::shared_ptr<SwitchComponent> switchComp;
    };

    void populate(const rapidjson::Value& result);
    // Shared optimistic-set + rollback-on-failure skeleton for both toggle kinds:
    // owns the RPC dispatch, the row lookup and the error rollback. The caller
    // supplies the success re-sync (resolveActual, whose payload shape differs per
    // RPC) and the success flash (onSuccess).
    void applyToggle(std::vector<ToggleRow>& rows, const std::string& method,
                     const std::string& keyField, const std::string& key, bool value,
                     const std::string& errorLabel,
                     std::function<bool(const rapidjson::Value& payload)> resolveActual,
                     std::function<void(bool actual)> onSuccess);
    void setFlag(const std::string& flag, const bool value);
    void setRaOption(const std::string& id, const bool value);

    std::string mSystem;
    std::shared_ptr<ImageComponent> mArt;
    std::shared_ptr<TextComponent> mBackendLine;
    std::shared_ptr<TextComponent> mManagedLine;
    std::shared_ptr<ComponentList> mList;
    std::vector<ToggleRow> mToggles;
    std::vector<ToggleRow> mRaToggles;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SYSTEMS_H
