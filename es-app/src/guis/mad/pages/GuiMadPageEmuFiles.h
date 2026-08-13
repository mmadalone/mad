//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmuFiles.h
//
//  Granular EMULATOR CONFIG + DATA backup & restore (deck-patches, P7): the tickable GROUP list for ONE
//  emulator (Config / Controller / Per-game / Keys / Saves & memory cards / Textures & mods). Each row is a
//  GROUP with its size; ticking a group selects all its files. Groups default ON except the huge opt-in ones
//  (textures / mods / Wii NAND / Xbox HDD), which show their size and default OFF. X backs up (backup mode)
//  or restores (restore mode) the ticked groups' files via the durable root GuiMadPageEmu. Backend:
//  emucfg.files, then granular.backup_emucfg / cloud.push_emucfg / granular.restore(category="emucfg").
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_FILES_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_FILES_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <string>
#include <vector>

class GuiMadPageEmu;

class GuiMadPageEmuFiles : public MadPage
{
public:
    GuiMadPageEmuFiles(GuiMadPanel* panel, GuiMadPageEmu* root, const std::string& source,
                       const std::string& emulator, const std::string& label, bool restore);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool consumesSectionNav() override { return false; } // leaving mid-op is allowed (op runs on the daemon)
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct Group {
        std::string key;   // config / controller / pergame / keys / saves / textures / ...
        std::string label;
        long long size;
        int count;
        bool selected;
        std::vector<std::string> rels; // every file rel this group covers (one X ticks them all)
    };
    std::string rowGlyph(const Group& g) const;
    unsigned int rowColor(const Group& g) const;
    std::string rowText(const Group& g) const;
    std::string headerText() const;

    void ensureWidgets();
    void populate();
    void toggleAt(int i);
    void act();  // X: back up / restore the ticked groups (via the durable root)

    GuiMadPageEmu* mRoot;
    std::string mSource;  // "live" (backup) or a backup folder / "cloud:<ts>" (restore)
    std::string mEmulator;
    std::string mLabel;
    bool mRestore;

    std::vector<Group> mGroups;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_FILES_H
