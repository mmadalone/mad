//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArchGame.cpp  (deck-patches)
//
//  RA-specific subclass of GuiMadPagePergameBrowser; the two-pane media+info
//  layout lives in the base.
//

#include "guis/mad/pages/GuiMadPageRetroArchGame.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/pages/GuiMadPageBackends.h"           // GuiMadPageBackendChoice (core picker)
#include "guis/mad/pages/GuiMadPageEmuSettings.h"        // handheld input editor (ragamehh)
#include "guis/mad/pages/GuiMadPageStandaloneSections.h" // Section

#include <algorithm>

namespace
{
    // Column layout matches the backend's own "Settings      default" style
    // (retroarch_game_cmds.py _settings_line/_input_line/_controllers_line
    // fallbacks) so an all-default game reads identically to a partially-
    // overridden one.
    const char* const kDefaultSummary {"Settings      default\n"
                                       "Input remap   default\n"
                                       "Controllers   default (global)"};
} // namespace

GuiMadPageRetroArchGame::GuiMadPageRetroArchGame(GuiMadPanel* panel, const std::string& system,
                                                 bool handheld)
    : GuiMadPagePergameBrowser {panel, system, "ragame", system, ""}
    , mHandheld {handheld}
{
}

void GuiMadPageRetroArchGame::build()
{
    // Entering the system always starts targeting All cores (mCores is re-parsed
    // on every requestGames() incl. the onChildPopped() refresh, but mEditCore is
    // only reset HERE -- so picking a core then editing a game isn't silently
    // reverted by the post-edit re-fetch).
    mEditCore.clear();
    GuiMadPagePergameBrowser::build();
}

void GuiMadPageRetroArchGame::writeGamesArgs(MadJson::Writer& w)
{
    w.Key("system");
    w.String(mSystem.c_str(), static_cast<rapidjson::SizeType>(mSystem.length()));
}

std::string GuiMadPageRetroArchGame::gameId(const rapidjson::Value& g)
{
    return mSystem + ":" + MadJson::getString(g, "stem");
}

void GuiMadPageRetroArchGame::parsePayloadExtra(const rapidjson::Value& payload)
{
    // "cores" is a top-level array, a SIBLING of "games" -- every core name the
    // system has games under (>1 == multi-core: the Settings/Input remap editors
    // can then target one specific core via mEditCore).
    mCores.clear();
    const rapidjson::Value& coresArr {MadJson::getMember(payload, "cores")};
    if (coresArr.IsArray())
        for (const rapidjson::Value& c : coresArr.GetArray())
            if (c.IsString())
                mCores.emplace_back(c.GetString(), c.GetStringLength());
    // If the on-disk core set changed while this page was open, drop a now-stale
    // picked core so we never target a vanished core dir on the next edit.
    if (!mEditCore.empty() &&
        std::find(mCores.begin(), mCores.end(), mEditCore) == mCores.end())
        mEditCore.clear();
}

void GuiMadPageRetroArchGame::perGameExtra(const rapidjson::Value& g, Game& out)
{
    // "Core: <name>" subtitle -- the core the LAUNCHED command actually reads
    // (retroarch_cfg.launched_core); omitted for a standalone/unresolvable core.
    const std::string core {MadJson::getString(g, "core")};
    out.sub = core.empty() ? "" : "Core: " + core;
}

std::string GuiMadPageRetroArchGame::previewHeadLines(const Game& /*g*/)
{
    // Only shown on a multi-core system: which core the per-game editors target.
    return mCores.size() > 1
               ? "\nEdit: " + (mEditCore.empty() ? std::string("All cores") : mEditCore)
               : std::string();
}

std::string GuiMadPageRetroArchGame::defaultSummary()
{
    return kDefaultSummary;
}

void GuiMadPageRetroArchGame::openGame(int i)
{
    if (i < 0 || i >= static_cast<int>(mShown.size()))
        return;
    const std::string tid {mShown[i].id}; // "<system>:<stem>"
    const std::string name {mShown[i].name};

    // On-the-go per-game HANDHELD input: skip the permanent Settings/Input/Controllers menu and open
    // the handheld input editor (ragamehh) for this game directly.
    if (mHandheld) {
        mPanel->pushPage(new GuiMadPageEmuSettings(mPanel, name + " - Handheld input", "ragamehh",
                                                   "titleid", tid, mEditCore));
        return;
    }

    std::vector<GuiMadPageStandaloneSections::Section> subs;

    // mEditCore ("" == All cores) targets the two RA-editor RPCs only --
    // Controllers below stays core-agnostic.
    GuiMadPageStandaloneSections::Section settings;
    settings.label = "Settings";
    settings.kind = "pergame_settings";
    settings.arg = "ragameset";
    settings.title = name + " — Settings";
    settings.ctxVal = tid;
    settings.core = mEditCore;
    subs.push_back(settings);

    GuiMadPageStandaloneSections::Section remap;
    remap.label = "Input remap";
    remap.kind = "pergame_settings";
    remap.arg = "ragamein";
    remap.title = name + " — Input remap";
    remap.ctxVal = tid;
    remap.core = mEditCore;
    subs.push_back(remap);

    GuiMadPageStandaloneSections::Section controllers;
    controllers.label = "Controllers";
    controllers.kind = "pergame_priority";
    controllers.title = name + " — Controllers";
    controllers.ctxVal = tid;
    subs.push_back(controllers);

    mPanel->pushPage(new GuiMadPageStandaloneSections(mPanel, name, subs));
}

bool GuiMadPageRetroArchGame::onExtraButton(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("x", input) && mCores.size() > 1) {
        openCorePicker();
        return true;
    }
    return false;
}

void GuiMadPageRetroArchGame::extraHelpPrompts(std::vector<HelpPrompt>& prompts)
{
    if (mCores.size() > 1)
        prompts.push_back(HelpPrompt("x", "core"));
}

void GuiMadPageRetroArchGame::openCorePicker()
{
    if (mCores.size() <= 1)
        return; // single-core system: nothing meaningful to pick.

    std::vector<std::pair<std::string, std::string>> options;
    options.emplace_back(std::string(),
                         "All cores (overwrites every core)"); // value MUST stay "" == All cores.
    for (const std::string& c : mCores)
        options.emplace_back(c, c);

    const std::string curLabel {mEditCore.empty() ? std::string("All cores") : mEditCore};
    std::weak_ptr<int> alive {pageAlive()};
    mPanel->pushPage(new GuiMadPageBackendChoice(
        mPanel, "Pick a core", "current: " + curLabel, options, mEditCore,
        [this, alive](const std::string& value) {
            if (alive.expired())
                return;
            mEditCore = value;
            updatePreview();
        }));
}
