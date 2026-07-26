//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBios.h
//
//  Granular BIOS backup & restore (deck-patches): the durable ROOT of a BIOS op. Shows the per-system BIOS
//  bucket tiles (bios.systems), drills into ONE bucket's file list (GuiMadPageBiosFiles), and OWNS the
//  running backup/restore so a popped file list never orphans it. Backup mode: source="live", X on the file
//  list backs up to a chosen folder (granular.backup_bios). Restore mode: source=a chosen backup, X restores
//  over the live BIOS (granular.restore category="bios", rule-5 warned). Cloud BIOS is a later slice.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;

class GuiMadPageBios : public MadPage
{
public:
    // mode: "backup" (source="live") or "restore" (source=a backup folder).
    GuiMadPageBios(GuiMadPanel* panel, const std::string& mode, const std::string& source);
    ~GuiMadPageBios() override;

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    bool consumesSectionNav() override { return mRunning; }
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

    bool busy() const { return mRunning; }
    // Called by the file-list leaf: back up / restore the ticked files of one bucket. The op runs HERE so
    // it outlives a popped leaf. rels are "bios/<path>" keys.
    void startBiosBackup(const std::string& bucket, const std::vector<std::string>& rels);
    void startBiosRestore(const std::string& bucket, const std::vector<std::string>& rels);

private:
    struct Bucket {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };
    void fetchSystems();
    void rebuildSystems();
    void onPickBucket(const std::string& key);
    void attachRunStream(const std::string& token, bool restore);
    void clearRunStream();

    std::string mMode;    // "backup" | "restore"
    std::string mSource;  // "live" | a backup folder
    bool mBackup;

    std::vector<Bucket> mBuckets;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BIOS_H
