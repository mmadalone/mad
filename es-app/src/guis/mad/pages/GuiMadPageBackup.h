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
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_BACKUP_H
