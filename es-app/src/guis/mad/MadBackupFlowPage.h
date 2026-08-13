//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadBackupFlowPage.h
//
//  MAD control panel: the shared backup / restore flow (deck-patches). Every
//  granular category page - System, Controller config, ES-DE settings, BIOS,
//  emulator config - is the same flow around a different category: a
//  destination / source bar across the top (X toggles On-this-Deck <-> MEGA,
//  Y changes the folder or picks a different dated backup), a cloud-as-default
//  probe, a source picker with a browse escape hatch, and one run-stream
//  handler driving the footer. That flow lived as six hand-maintained copies
//  until phase 4b of the 2026-08-12 audit; this is the one copy.
//
//  What stays with the pages is what genuinely differs: the CONTENT layer (a
//  tickable group list vs a tile grid) and the handful of behaviours below that
//  are per-category by design.
//

#ifndef ES_APP_GUIS_MAD_MAD_BACKUP_FLOW_PAGE_H
#define ES_APP_GUIS_MAD_MAD_BACKUP_FLOW_PAGE_H

#include "guis/mad/MadPage.h"

#include <memory>
#include <string>
#include <vector>

class TextComponent;

class MadBackupFlowPage : public MadPage
{
public:
    bool busy() const { return mRunning; }

protected:
    // Everything that is per-category DATA rather than per-category BEHAVIOUR. Pages brace-init
    // one of these in their constructor; nothing here is read before build().
    struct Params {
        std::string category;      // "system" - the granular.* category key, never a literal length
        std::string cloudSourcesRpc; // "system.cloud_sources"
        std::string backupRpc;     // "granular.backup_system"
        std::string cloudPushRpc;  // "cloud.push_system"
        std::string contentRpc;    // "system.groups" - the page's own content listing
        std::string noun;          // "system config"  -> "Backing up system config..."
        std::string sourceNoun;    // "system-config"   -> "No system-config backup on MEGA yet."
        std::string itemNoun;      // " item(s)"        -> "Restored 4 item(s)."
        // The restore footer status. Its own field because ES-DE settings STAGE rather than
        // restore ("Staging ES-DE settings..."), which is a different verb, not a different noun.
        std::string restoreStatus;
    };

    MadBackupFlowPage(GuiMadPanel* panel, const std::string& title, const std::string& mode,
                      const Params& params);

    // ── the destination (backup) / source (restore) bar ──────────────────────
    void ensureBar();
    void refreshBar();
    std::string barText() const;
    // Cloud-as-default: probe cloud.status FIRST so the bar never flashes On-this-Deck before
    // switching to MEGA. A manual X/Y during the probe wins.
    void resolveDefaultDestination();
    void toggleCloud();
    void changeTarget();
    void resolveDefaultSource();
    void applySource(const std::string& id, const std::string& created, int count);
    void openSourcePicker();
    void browseForSource();

    // ── the run stream ───────────────────────────────────────────────────────
    void attachRunStream(const std::string& token, bool restore, bool cloud);
    void clearRunStream();

    // ── hooks ────────────────────────────────────────────────────────────────
    // Re-read the page's content for the current source (the group list / the tile grid).
    virtual void refetchForSource() = 0;
    // Drop the content built for the PREVIOUS source. Called before a source resolve and again
    // when the resolve finds nothing, so it must be safe to run twice.
    virtual void teardownContent() = 0;
    // Appended to the restore-success flash ("The emulator picks it up on its next launch.").
    virtual std::string restoreDoneSuffix() const { return ""; }
    // The whole restore-success report. The default is the counts + the orphaned-files dialog;
    // ES-DE settings replaces it wholesale because a staged restore reports something else.
    virtual void onRestoreSucceeded(const rapidjson::Value& data);
    // Most pages prefix the backend's message; emulator config surfaces it verbatim, because the
    // per-emulator EBUSY guard's text ("close PCSX2 before restoring its config") IS the answer.
    virtual std::string restoreStartErrorText(const std::string& message) const
    {
        return "Couldn't start restore: " + message;
    }
    virtual int restoreStartErrorMs() const { return 5000; }

    const Params mParams;
    std::string mMode;            // "backup" | "restore"
    std::string mSource;          // "live" | a backup folder | "cloud:<ts>"
    bool mBackup;

    // dest/source bar state.
    bool mCloud {false};          // the destination/source is MEGA
    bool mDestTouched {false};    // user pressed X/Y -> the auto cloud-default promote must not override
    bool mDestResolved {false};   // false -> the bar shows "(checking...)" until cloud.status resolves
    std::string mDest;            // backup: the remembered local destination folder
    std::string mSrcCreated;      // restore: the current source's timestamp
    int mSrcCount {0};            // restore: the current source's file count
    bool mHasSource {false};
    bool mChecking {false};
    int mSrcGen {0};              // bumped when the source changes; a stale in-flight resolve bails
    std::shared_ptr<TextComponent> mBar;

    bool mRunning {false};
    bool mRestorePreviewing {false};
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_MAD_BACKUP_FLOW_PAGE_H
