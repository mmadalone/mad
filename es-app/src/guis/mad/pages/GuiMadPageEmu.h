//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmu.h
//
//  Granular EMULATOR CONFIG + DATA backup & restore (deck-patches, P7): the durable ROOT of an emulator-
//  config op. Reached AFTER the destination/source is chosen (GuiMadPageChooseTarget - emucfg is VERSIONED,
//  so the chooser shows a date list on restore), so it opens straight on the per-EMULATOR tiles
//  (emucfg.systems - PCSX2 / Dolphin / Cemu / RetroArch / ...). Drills into ONE emulator's tickable GROUP
//  list (GuiMadPageEmuFiles: Config / Controller / Per-game / Keys / Saves & memory cards / Textures & mods)
//  and OWNS the running backup/restore so a popped leaf never orphans it. Backup: X on the group list backs
//  up to the resolved destination (a local folder -> granular.backup_emucfg, or MEGA -> cloud.push_emucfg).
//  Restore: X restores over the live config from the resolved source, rule-5 warned, and REFUSES (per the
//  backend guard) if that emulator is running. Cloud emucfg uses its OWN remote base (emucfg-backups).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H

#include "guis/mad/MadPage.h"
#include "guis/mad/pages/GuiMadPageChooseTarget.h" // MadTarget (the resolved destination / source)

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;

// One ticked backup item: a file rel plus the display GROUP it belongs to (the backup manifest tags each
// item with its group so a restore can regroup it). Restore only needs the rel (system = the emulator).
struct EmuItem
{
    std::string group;
    std::string rel;
};

class GuiMadPageEmu : public MadPage
{
public:
    // mode: "backup" (source="live", backs up to `target`) or "restore" (source = target.source, a backup
    // folder or "cloud:<ts>").
    GuiMadPageEmu(GuiMadPanel* panel, const std::string& mode, const MadTarget& target);
    ~GuiMadPageEmu() override;

    void build() override;
    bool input(InputConfig* config, Input input) override;
    bool onBackPressed() override;
    bool consumesSectionNav() override { return false; } // leaving mid-op is allowed (op runs on the daemon)
    void pageScroll(int direction) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onSaveFocus() override;
    void onRestoreFocus() override;

    bool busy() const { return mRunning; }
    // Called by the group-list leaf: back up / restore ONE emulator's ticked groups. The op runs HERE so it
    // outlives a popped leaf. `items` carry (group, rel) for backup; restore keys off the emulator + rels.
    void startEmuBackup(const std::string& emulator, const std::vector<EmuItem>& items);
    void startEmuRestore(const std::string& emulator, const std::vector<std::string>& rels);

private:
    struct Emu {
        std::string key;
        std::string label;
        std::string art;
        int count;
    };

    void fetchEmulators();
    void rebuildEmulators();
    void onPickEmulator(const std::string& key);
    // the two backup destination branches (each claims mRunning only when its real backup fires).
    void beginEmuBackupLocal(const std::string& emulator, const std::vector<EmuItem>& items,
                             const std::string& dest);
    void beginEmuBackupCloud(const std::string& emulator, const std::vector<EmuItem>& items);
    // cloud=true: the op streams from cloud.push_emucfg (terminal "Backed up to MEGA." - no file count).
    void attachRunStream(const std::string& token, bool restore, bool cloud = false);
    void clearRunStream();

    std::string mMode;    // "backup" | "restore"
    std::string mSource;  // "live" (backup) | a backup folder | "cloud:<ts>" (restore)
    bool mBackup;
    bool mCloud;          // backup: the destination is MEGA (else mDest is a local folder)
    std::string mDest;    // backup: the resolved local destination folder ("" when mCloud)

    std::vector<Emu> mEmulators;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H
