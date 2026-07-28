//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmu.h
//
//  Granular EMULATOR CONFIG + DATA backup & restore (deck-patches, P7): the durable ROOT of an emulator-
//  config op. Opens straight on the per-EMULATOR tiles (emucfg.systems - PCSX2 / Dolphin / Cemu / RetroArch
//  / ...) with a DESTINATION / SOURCE bar across the top (X toggles On-this-Deck <-> MEGA; Y changes the
//  folder on backup, or picks a different dated backup on restore). Drills into ONE emulator's tickable GROUP
//  list (GuiMadPageEmuFiles: Config / Controller / Per-game / Keys / Saves & memory cards / Textures & mods)
//  and OWNS the running backup/restore so a popped leaf never orphans it. Backup: X on the group list backs
//  up to the bar's destination (a local folder -> granular.backup_emucfg, or MEGA -> cloud.push_emucfg).
//  Restore: X restores over the live config from the bar's source (a dated local backup or "cloud:<ts>"),
//  rule-5 warned, and REFUSES (per the backend guard) if that emulator is running. Cloud emucfg uses its OWN
//  remote base (emucfg-backups). Emulator config is VERSIONED (dated snapshots), so the restore source picker
//  lists dates.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <string>
#include <vector>

class MadTileGrid;
class TextComponent;

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
    // mode: "backup" (source="live", destination bar at the top) or "restore" (source bar at the top).
    GuiMadPageEmu(GuiMadPanel* panel, const std::string& mode);
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

    // The destination (backup) / source (restore) bar at the top of the emulator page.
    void ensureBar();
    void refreshBar();
    std::string barText() const;
    void toggleCloud();     // X: On this Deck <-> MEGA
    void changeTarget();    // Y: backup = change folder; restore = pick a different backup
    void resolveDefaultSource();
    void openSourcePicker();
    void applySource(const std::string& id, const std::string& created, int count);
    void browseForSource();

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

    // dest/source bar state.
    bool mCloud {false};
    std::string mDest;       // backup: the remembered local destination folder
    std::string mSrcCreated; // restore: the current source's timestamp
    int mSrcCount {0};       // restore: the current source's file count
    bool mHasSource {false};
    bool mChecking {false};
    // Bumped whenever the source changes; an in-flight resolve / systems fetch bails if superseded, so a
    // stale reply can't overwrite the current source.
    int mSrcGen {0};
    std::shared_ptr<TextComponent> mBar;

    std::vector<Emu> mEmulators;
    std::shared_ptr<MadTileGrid> mGrid;
    int mGridCookie {0};

    bool mRunning {false};
    bool mRestorePreviewing {false}; // a restore_preview round-trip is in flight (guards a double X)
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_H
