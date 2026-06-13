//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageDaphne.h
//
//  MAD control panel: Daphne / Hypseus controls (deck-patches). Maps the
//  X-Arcade to laserdisc-game actions: focus a row, A = press-to-bind (the
//  daemon captures one cabinet press via hypseus_capture.py, input.lock
//  bracketing it), X = clear. Global map or per-game overrides; the editing
//  buffer lives in the daemon (Tk _dp_hi parity).
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_DAPHNE_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_DAPHNE_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <map>
#include <string>
#include <vector>

class GuiMadPageDaphne : public MadLightgunPageBase
{
public:
    GuiMadPageDaphne(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    void onChildPopped() override {} // The game pick reloads explicitly.

private:
    struct ActionRow {
        std::string action;
        std::string label;
        std::string display;
        bool warn {false};
    };
    struct Game {
        std::string gamedir;
        std::string base;
        std::string name;
    };

    void load(const std::string& scope, const std::string& gamedir,
              const std::string& base, bool announce = false);
    void parse(const rapidjson::Value& result);
    void relayout();
    void applyRowUpdate(const rapidjson::Value& row);
    void bindAction(const std::string& action);
    void clearAction(const std::string& action);
    std::string rowText(const ActionRow& row) const;

    // Page state parsed from daphne.load (+ patched by bind/clear responses).
    std::string mScope; // "global" | "game"
    std::string mBase;
    std::string mGameName;
    std::string mCaption;
    std::string mHint;
    bool mSeekInstant;
    bool mAdvOpen;
    std::map<std::string, ActionRow> mRows;
    std::map<std::string, std::vector<std::string>> mSections;
    std::vector<Game> mGames;
    // Focus-control index → bound action ("" for non-row controls); X = clear.
    std::vector<std::string> mControlActions;
    bool mBinding;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_DAPHNE_H
