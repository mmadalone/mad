//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEsdeGamelists.h
//
//  The per-system drill for the "Game favorites & metadata" group (deck-patches, P6). A tickable list of
//  systems (esde.gamelist_systems); A toggles a system's gamelist.xml rel into the root GuiMadPageEsde's
//  selection set, B returns. No op runs here - it only edits the set the root backs up / restores.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_GAMELISTS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_GAMELISTS_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <set>
#include <string>
#include <vector>

class GuiMadPageEsde;

class GuiMadPageEsdeGamelists : public MadPage
{
public:
    GuiMadPageEsdeGamelists(GuiMadPanel* panel, GuiMadPageEsde* root, const std::string& source);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Sys {
        std::string system;
        std::string label;
        std::string rel;   // "esde/gamelists/<system>/gamelist.xml" - the true key
        long long size;
        bool selected;
    };
    std::string rowText(const Sys& s) const;
    std::string headerText() const;
    void ensureWidgets();
    void populate();
    void toggleAt(int i);

    GuiMadPageEsde* mRoot;
    std::set<std::string>* mSel; // the root's ticked-gamelist-rels set
    std::string mSource;

    std::vector<Sys> mSystems;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    int mFocusCookie {0};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_ESDE_GAMELISTS_H
