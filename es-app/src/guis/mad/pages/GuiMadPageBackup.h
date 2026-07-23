//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackup.h
//
//  MAD control panel: Backup / Restore (deck-patches). Full-system backup via
//  deck-backup.sh (11 include toggles with streamed per-category sizes + a
//  live tally; output lines stream into the footer) and the router-config
//  snapshot/restore quartet from lib/mad_backup. The destructive actions go
//  through a GuiMsgBox confirm.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase.

#include <map>
#include <string>
#include <utility>
#include <vector>

class GuiMadPageBackup : public MadLightgunPageBase
{
public:
    GuiMadPageBackup(GuiMadPanel* panel);
    ~GuiMadPageBackup();

    void build() override;

private:
    void rebuild(); // Pure local state — safe to re-run on size pushes.
    std::string chipLabel(const std::string& key) const;
    void updateTally();
    void onSizePush(const rapidjson::Value& data);
    void runFull();

    // Cloud (MEGA) section: state is fetched async, so the section renders from
    // members and re-lays-out (deferRelayout -> rebuild) as cloud.status /
    // cloud.servers land, mirroring how the per-category sizes stream in.
    void fetchCloud();        // issue cloud.status + cloud.servers
    void buildCloudSection(); // render from the fetched state (called by rebuild)
    void pickServer();        // open the A-pressable list of MEGA S4 servers
    void setServer(const std::string& id);
    void setCloudToggle(const std::string& which, const bool on);
    void cloudStream(const std::string& method, const std::string& startStatus,
                     const std::string& okMsg);
    bool cloudGuard(); // busy OR not-connected guard for the S4 actions

    bool busyGuard(); // True (with a footer note) while a full backup streams.
    void confirmThen(const std::string& text, const std::function<void()>& action);
    MadBackend::ResponseCallback resultFlash();
    static std::string human(const long long bytes);

    std::map<std::string, bool> mInclude;
    std::map<std::string, long long> mSizes;
    bool mSizesDone;
    bool mRunning; // A full backup is streaming.
    std::string mSizesToken;
    std::string mRunToken;
    std::shared_ptr<TextComponent> mTally;
    std::vector<std::shared_ptr<MadChipRow>> mChipRows;

    // Cloud (MEGA) state (fetched async; the section renders once these arrive).
    bool mCloudStatusLoaded {false};
    bool mCloudServersLoaded {false};
    bool mCloudConnected {false};
    bool mCloudOnExit {false};
    bool mCloudTimer {false};
    std::string mCloudServerId;
    std::string mCloudServerLabel;
    std::string mCloudLastBackup;
    std::vector<std::pair<std::string, std::string>> mCloudServers; // (id, label)
    std::shared_ptr<MadChipRow> mCloudToggleRow;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_H
