//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageMediaKinds.h
//
//  Granular Backup & Restore (deck-patches, P4): the per-KIND media drill. Reached with Y on the "Media"
//  row of GuiMadPageAssetList. It fetches the game's media kinds (granular.game_media - box art / marquee /
//  video / ...) for the current source (live for backup, the chosen backup for restore) and ticks the
//  selected kinds straight into the owning asset list's per-kind set. A transient leaf: it owns no op and
//  writes nothing; the owner reads its set back when this page pops. The owner then sends coarse "media"
//  (all kinds) or per-kind "media.<kind>" backup/restore keys.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MEDIA_KINDS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MEDIA_KINDS_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <set>
#include <string>
#include <vector>

class GuiMadPageAssetList;

class GuiMadPageMediaKinds : public MadPage
{
public:
    GuiMadPageMediaKinds(GuiMadPanel* panel, GuiMadPageAssetList* owner, const std::string& source,
                         const std::string& system, const std::string& stem);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool consumesSectionNav() override { return false; } // no running op here; leaving is free
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Kind {
        std::string key;   // covers / marquees / ... (the media_for kind name)
        std::string label; // "Box art", "Marquee", ...
        long long size;
    };
    std::string rowText(const Kind& k) const;
    std::string headerText() const;
    void ensureWidgets();
    void populate();
    void toggleAt(int i);

    GuiMadPageAssetList* mOwner;
    std::string mSource;
    std::string mSystem;
    std::string mStem;
    std::set<std::string>* mSel {nullptr}; // -> the owner's per-kind media set (ticked in place)

    std::vector<Kind> mKinds;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_MEDIA_KINDS_H
