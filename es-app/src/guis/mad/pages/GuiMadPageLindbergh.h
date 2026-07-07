//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLindbergh.h
//
//  MAD control panel: Sega Lindbergh per-game input binder (deck-patches). Clones
//  the Daphne binder's focus-row / A=bind / X=clear flow, but lindbergh-loader has
//  NO global config, so it is game-pick-only (a game picker, then that game's rows).
//  Rows come from lindbergh-profiles.json (friendly label -> lindbergh.ini [EVDEV]
//  key); buttons bind with a press, axes (wheel/pedals/stick) with a move. Buffered:
//  edits stage in the daemon buffer, SAVE writes the ini (.bak), CANCEL reverts.
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <map>
#include <string>
#include <vector>

class GuiMadPanel;

class GuiMadPageLindbergh : public MadLightgunPageBase
{
public:
    GuiMadPageLindbergh(GuiMadPanel* panel);

    void build() override;
    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;
    bool madSave() override;
    bool madCancel() override;
    bool hasUnsavedEdits() const override { return !mTitleId.empty() && mDirty; }
    void onChildPopped() override {} // The game pick reloads explicitly.

private:
    struct Row {
        std::string key;     // the lindbergh.ini [EVDEV] key, e.g. PLAYER_1_BUTTON_3
        std::string label;   // friendly name, e.g. "Player 1 Grenade"
        std::string display; // current token or "— unbound"
        bool warn {false};
        bool axis {false};   // true = ANALOGUE_n (bind by moving, not pressing)
    };
    struct Game {
        std::string titleid;
        std::string name;
    };

    void load(const std::string& titleid, bool announce = false);
    void parse(const rapidjson::Value& result);
    void relayout();
    void applyRowUpdate(const rapidjson::Value& row);
    void bindAction(const std::string& key);
    void clearAction(const std::string& key);
    std::string rowText(const Row& row) const;
    void saveOrCancel(const char* method);
    void gunDriver(const char* action); // start/stop the Sinden pipeline (reuses sinden.driver)
    void testFire();                     // capture-mode readout: confirm the gun is emitting
    void captureQuitCombo();             // per-game hold-to-quit combo (reuses the global combo capture)
    void clearQuitCombo();

    std::string mTitleId;  // empty = no game picked yet
    std::string mGameName;
    std::string mCaption;
    bool mGun {false};
    std::string mQuitScope;    // [quit_combo.<scope>] key for this game (lindbergh-<titleid>)
    std::string mQuitDisplay;  // current quit-combo button names, or "" when unset
    std::map<std::string, Row> mRows;                       // key -> row
    std::map<std::string, std::vector<std::string>> mSections; // section -> keys
    std::vector<Game> mGames;
    std::vector<std::string> mControlActions; // control index -> key ("" = not a row)
    bool mBinding {false};
    bool mDirty {false}; // true once a bind/clear is staged in the daemon buffer, unsaved
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_LINDBERGH_H
