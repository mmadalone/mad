//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSidebar.h
//
//  MAD control panel: "Sidebar" section (deck-patches). Lets the user override which
//  capability-gated sidebar rows are shown — Auto (the default; hardware/data decides),
//  Always show, or Always hide. Writes install.conf FORCE_SHOW_*/FORCE_HIDE_* via the
//  backend (sidebar.set); the panel re-filters its sidebar on the next open.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SIDEBAR_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SIDEBAR_H

#include "components/ComponentList.h"
#include "components/TextComponent.h"
#include "guis/mad/MadPage.h"

#include <memory>
#include <string>

class GuiMadPanel;

class GuiMadPageSidebar : public MadPage
{
public:
    GuiMadPageSidebar(GuiMadPanel* panel);

    void build() override;

private:
    void requestSections();
    void populate(const rapidjson::Value& result);
    void setMode(const std::string& key, const std::string& mode);

    std::shared_ptr<TextComponent> mIntro;   // standalone child -> must outlive build()
    std::shared_ptr<ComponentList> mList;     // owns its row components (incl. the option lists)
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_SIDEBAR_H
