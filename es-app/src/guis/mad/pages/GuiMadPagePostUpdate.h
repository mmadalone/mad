//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePostUpdate.h
//
//  MAD control panel: reapply system setup after a SteamOS update (deck-patches).
//
//  A SteamOS system update resets the immutable root, wiping the persistence this rig needs
//  (Samba, Sinden deps + udev, the input group, suspend=deep, ...). deck-post-update.sh re-applies
//  it but needs sudo. This page runs it IN ES-DE: it asks for the sudo password on the on-screen
//  keyboard (masked), streams verbose per-step progress, and offers a reboot when done - replacing
//  the old "go to Desktop Mode and run it by hand" nudge. Backed by the postupdate.* RPCs.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_POSTUPDATE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_POSTUPDATE_H

#include "guis/mad/MadPage.h"

#include <deque>
#include <string>
#include <vector>

class ButtonComponent;

class GuiMadPagePostUpdate : public MadPage
{
public:
    explicit GuiMadPagePostUpdate(GuiMadPanel* panel);
    ~GuiMadPagePostUpdate() override;

    void build() override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    // While a reapply runs, block EVERY exit route so the sudo run is never interrupted: the panel
    // consults onBackPressed() before it pops (input()-level B never reaches the page), and
    // consumesSectionNav() keeps the shoulder/trigger section-switch from leaving the page.
    bool onBackPressed() override;
    bool consumesSectionNav() override;

private:
    enum class State { Idle, Running, Done, DoneFailed };

    void fetchStatus();
    void layout();                      // (re)position the fixed widgets + the button row
    void rebuildButtons();              // the action row depends on mState
    void focusButton(int index);
    void promptPasswordThenRun();       // pop the masked keyboard, then startRun()
    void startRun(const std::string& password);
    void installStream(const std::string& token);
    void appendLog(const std::string& line);
    void setStatus(const std::string& text);
    void rebootNow();

    static const int kMaxLogLines {14};

    std::shared_ptr<TextComponent> mIntro;
    std::shared_ptr<TextComponent> mStatus;
    std::shared_ptr<TextComponent> mLog;
    std::deque<std::string> mLogLines;

    std::vector<std::shared_ptr<ButtonComponent>> mButtons;
    int mFocus {0};

    State mState {State::Idle};
    bool mStatusLoaded {false};
    bool mPasswordless {false};
    int mTriesLeft {3};
    std::vector<std::string> mMissing;
    std::vector<std::string> mFailed;
    std::string mRunToken;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_POSTUPDATE_H
