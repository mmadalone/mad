//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBiosFiles.h
//
//  Granular BIOS backup & restore (deck-patches): the tickable file list for ONE BIOS bucket (a per-system
//  group). A rel is the TRUE 'bios/<path>' key, so a file restores to its exact origin regardless of the
//  bucket that showed it. X backs up (backup mode) or restores (restore mode) the ticked files via the
//  durable root GuiMadPageBios. Backend: bios.files, then granular.backup_bios / granular.restore.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_FILES_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_FILES_H

#include "guis/mad/MadPage.h"
#include "guis/mad/widgets/MadVirtualList.h"

#include <string>
#include <vector>

class GuiMadPageBios;

class GuiMadPageBiosFiles : public MadPage
{
public:
    GuiMadPageBiosFiles(GuiMadPanel* panel, GuiMadPageBios* root, const std::string& source,
                        const std::string& bucket, const std::string& label, bool restore);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool consumesSectionNav() override;
    void pageScroll(int direction) override;
    void onSaveFocus() override;
    void onRestoreFocus() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    struct File {
        std::string rel;   // "bios/<path>" - the true restore key
        std::string name;  // basename, for display
        long long size;
        bool selected;
    };
    std::string rowGlyph(const File& f) const;
    unsigned int rowColor(const File& f) const;
    std::string rowText(const File& f) const;
    std::string headerText() const;

    void ensureWidgets();
    void populate();
    void toggleAt(int i);
    void act();  // X: back up / restore the ticked files (via the durable root)

    GuiMadPageBios* mRoot;
    std::string mSource;  // "live" (backup) or a backup folder (restore)
    std::string mBucket;
    std::string mLabel;
    bool mRestore;

    std::vector<File> mFiles;
    std::shared_ptr<TextComponent> mHeader;
    std::shared_ptr<MadVirtualList> mList;
    int mFocusCookie {0};
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_FILES_H
