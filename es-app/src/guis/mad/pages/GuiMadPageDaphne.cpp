//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageDaphne.cpp
//
//  MAD control panel: Daphne / Hypseus controls (deck-patches).
//

#include "guis/mad/pages/GuiMadPageDaphne.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice.
#include "utils/FileSystemUtil.h"

#include <algorithm>

namespace
{
    // The scope persists across page entries within a session (Tk _dp_scope).
    std::string sScope {"global"};
    std::string sGamedir;
    std::string sBase;
} // namespace

GuiMadPageDaphne::GuiMadPageDaphne(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "DAPHNE / HYPSEUS CONTROLS"}
    , mSeekInstant {false}
    , mAdvOpen {false}
    , mBinding {false}
{
}

void GuiMadPageDaphne::build()
{
    setLoadingText("Loading the Hypseus map…");
    load(sScope, sGamedir, sBase);
}

void GuiMadPageDaphne::load(const std::string& scope, const std::string& gamedir,
                            const std::string& base)
{
    pageRequest(
        "daphne.load",
        [scope, gamedir, base](MadJson::Writer& writer) {
            writer.Key("scope");
            writer.String(scope.c_str(), static_cast<rapidjson::SizeType>(scope.length()));
            if (scope == "game") {
                writer.Key("gamedir");
                writer.String(gamedir.c_str(),
                              static_cast<rapidjson::SizeType>(gamedir.length()));
                writer.Key("base");
                writer.String(base.c_str(), static_cast<rapidjson::SizeType>(base.length()));
            }
        },
        [this, scope, gamedir, base](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                // A stale per-game scope (game removed): fall back to global.
                if (scope == "game") {
                    sScope = "global";
                    sGamedir.clear();
                    sBase.clear();
                    load("global", "", "");
                    return;
                }
                footer()->setStatus("Couldn't load the Hypseus map: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            sScope = scope;
            sGamedir = gamedir;
            sBase = base;
            parse(payload);
            relayout();
            footer()->setStatus("Editing the " + mCaption);
        },
        10000);
}

void GuiMadPageDaphne::parse(const rapidjson::Value& result)
{
    mScope = MadJson::getString(result, "scope", "global");
    mBase = MadJson::getString(result, "base");
    mGameName = MadJson::getString(result, "game_name");
    mCaption = MadJson::getString(result, "caption");
    mHint = MadJson::getString(result, "hint");
    mSeekInstant = MadJson::getBool(result, "seek_instant");

    mRows.clear();
    const rapidjson::Value& rows {MadJson::getMember(result, "rows")};
    if (rows.IsObject()) {
        for (auto it = rows.MemberBegin(); it != rows.MemberEnd(); ++it) {
            ActionRow row;
            row.action = MadJson::getString(it->value, "action");
            row.label = MadJson::getString(it->value, "label", row.action);
            row.display = MadJson::getString(it->value, "display");
            row.warn = MadJson::getBool(it->value, "warn");
            mRows[row.action] = row;
        }
    }

    mSections.clear();
    const rapidjson::Value& sections {MadJson::getMember(result, "sections")};
    if (sections.IsObject()) {
        for (auto it = sections.MemberBegin(); it != sections.MemberEnd(); ++it) {
            std::vector<std::string> actions;
            if (it->value.IsArray()) {
                for (rapidjson::SizeType i {0}; i < it->value.Size(); ++i) {
                    if (it->value[i].IsString())
                        actions.emplace_back(it->value[i].GetString());
                }
            }
            mSections[it->name.GetString()] = actions;
        }
    }

    mGames.clear();
    const rapidjson::Value& games {MadJson::getMember(result, "games")};
    if (games.IsArray()) {
        for (rapidjson::SizeType i {0}; i < games.Size(); ++i)
            mGames.push_back({MadJson::getString(games[i], "gamedir"),
                              MadJson::getString(games[i], "base"),
                              MadJson::getString(games[i], "name")});
    }
}

std::string GuiMadPageDaphne::rowText(const ActionRow& row) const
{
    return row.label + " — " + row.display + (row.warn ? "  ⚠" : "");
}

void GuiMadPageDaphne::applyRowUpdate(const rapidjson::Value& row)
{
    ActionRow updated;
    updated.action = MadJson::getString(row, "action");
    updated.label = MadJson::getString(row, "label", updated.action);
    updated.display = MadJson::getString(row, "display");
    updated.warn = MadJson::getBool(row, "warn");
    mRows[updated.action] = updated;
}

void GuiMadPageDaphne::relayout()
{
    mControlActions.clear();

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    // mControlActions maps control index → action ("" = not an action row);
    // the final resize in relayout pads every other control with "".
    auto addActionRows = [this](const std::vector<std::string>& actions) {
        // One FLOW row per section: buttons run left-to-right and wrap, using
        // the screen width; mixed case keeps bind values like "axis 3"
        // readable. Left/right walk the section, up/down jump sections.
        std::vector<std::pair<std::string, std::function<void()>>> items;
        std::vector<std::string> order;
        for (const std::string& action : actions) {
            const auto it = mRows.find(action);
            if (it == mRows.end())
                continue;
            items.emplace_back(rowText(it->second),
                               [this, action] { bindAction(action); });
            order.emplace_back(action);
        }
        if (items.empty())
            return;
        addButtonRow(items, false);
        for (size_t i {0}; i < order.size(); ++i) {
            mControlActions.resize(mControls.size(), std::string {});
            mControlActions[mControls.size() - order.size() + i] = order[i];
        }
    };

    beginColumn();

    addBlock("Map your X-Arcade to Hypseus laserdisc-game controls: focus a row, press A "
             "to bind, then press the button on the cabinet (X clears a row). Save writes "
             "the map. No keyboard needed.",
             FONT_SIZE_SMALL, mMenuColorPrimary, smallHeight * 0.4f);

    // Scope selector (Tk: Global / This game…), side by side.
    header("Map");
    {
        std::vector<std::pair<std::string, std::string>> options;
        for (size_t i {0}; i < mGames.size(); ++i)
            options.emplace_back(std::to_string(i), mGames[i].name);
        std::weak_ptr<int> alive {pageAlive()};
        const std::string gameLabel {
            mScope == "game" ? "▸ THIS GAME:  " + (mGameName.empty() ? mBase : mGameName) :
                               "THIS GAME…"};
        addButtonRow(
            {{mScope == "global" ? "▸ GLOBAL" : "GLOBAL",
              [this] {
                  setLoadingText("Loading the global map…");
                  load("global", "", "");
              }},
             {gameLabel, [this, alive, options] {
            if (mGames.empty()) {
                footer()->flash("No Daphne games found under ~/ROMs/daphne.", 4000, true);
                return;
            }
            mPanel->pushPage(new GuiMadPageBackendChoice(
                mPanel, "Pick a Daphne game",
                "The map you edit next applies to ONLY this game (a per-game override).",
                options, "", [this, alive](const std::string& value) {
                    if (alive.expired())
                        return;
                    const size_t index {static_cast<size_t>(std::stoul(value))};
                    if (index >= mGames.size())
                        return;
                    setLoadingText("Loading " + mGames[index].name + "…");
                    load("game", mGames[index].gamedir, mGames[index].base);
                }));
              }}});
    }
    if (!mHint.empty())
        addBlock("ℹ  " + mHint, FONT_SIZE_MINI, mMenuColorGreen, smallHeight * 0.3f);

    header("Scene transitions");
    auto seekChip = addChips(
        {{"seek",
          "Instant (" + std::string(mScope == "game" ? mBase : "all laserdisc games") + ")",
          mSeekInstant}},
        false);
    std::weak_ptr<MadChipRow> weakSeek {seekChip};
    seekChip->setOnToggle([this, weakSeek](const std::string&, const bool on) {
        pageRequest(
            "daphne.seek_set",
            [on](MadJson::Writer& writer) {
                writer.Key("on");
                writer.Bool(on);
            },
            [this, weakSeek, on](bool ok, const rapidjson::Value& payload) {
                if (!ok) {
                    if (auto chip = weakSeek.lock())
                        chip->setChipState("seek", !on); // Roll back.
                    footer()->flash(MadJson::getString(payload, "message", "unknown error"),
                                    4000, true);
                    return;
                }
                mSeekInstant = MadJson::getBool(payload, "seek_instant", on);
                footer()->setStatus(MadJson::getString(payload, "message"));
            });
    });
    caption("Skips the emulated laserdisc SEEK delay between scenes (removes the loading "
            "wait). If a game's audio/timing ever feels off, switch it back off.");

    header("Seek-index builder");
    {
        const std::string arg {mScope == "game" ?
                                   Utils::FileSystem::getFileName(sGamedir) :
                                   std::string("all")};
        addButton(mScope == "game" ? "BUILD SEEK INDEX — " + mBase :
                                     "BUILD SEEK INDEXES — ALL GAMES",
                  [this, arg] {
                      pageRequest(
                          "daphne.build_index",
                          [arg](MadJson::Writer& writer) {
                              writer.Key("arg");
                              writer.String(arg.c_str(),
                                            static_cast<rapidjson::SizeType>(arg.length()));
                          },
                          [this](bool ok, const rapidjson::Value& payload) {
                              footer()->setStatus(
                                  MadJson::getString(payload, "message", "unknown error"),
                                  !ok);
                          });
                  });
    }
    caption("Builds the laserdisc seek indexes up front so scene changes never stop to "
            "\"seek\". Runs on-screen — you'll see it flash through scenes, then it "
            "returns here. One-time; ALL games can take several minutes; hold "
            "Start+Select to abort the current game.");

    header("Buttons");
    addActionRows(mSections["primary"]);

    header("Player 2 (coin + start only)");
    caption("Hypseus laserdisc games have NO separate P2 gameplay buttons — both players "
            "share the controls above. Only Coin 2 / Start 2 are P2-specific:");
    addActionRows(mSections["p2"]);

    header("Stick / steering (directions)");
    caption("Bind by pushing the stick (or turning the wheel) in that direction — an "
            "analog axis is captured; a digital d-pad records as a button. Driving games "
            "steer with Left/Right; their gas/brake are the action BUTTONS above.");
    addActionRows(mSections["directions"]);

    header("Advanced actions");
    caption("Rarely-needed extras — skills, service/test, pause, quit… Most setups never "
            "bind these; they still work from the keyboard keys in hypinput.ini.");
    addButton(mAdvOpen ? "HIDE ADVANCED" :
                         "SHOW (" + std::to_string(mSections["advanced"].size()) +
                             " ACTIONS)",
              [this] {
                  mAdvOpen = !mAdvOpen;
                  // Deferred: a synchronous relayout would destroy this button
                  // inside its own pressed callback.
                  deferRelayout([this] { relayout(); });
              });
    if (mAdvOpen)
        addActionRows(mSections["advanced"]);

    addButtonRow(
        {{"SAVE",
          [this] {
              pageRequest("daphne.save", nullptr,
                          [this](bool ok, const rapidjson::Value& payload) {
                              footer()->setStatus(
                                  MadJson::getString(payload, "message", "unknown error"),
                                  !ok);
                          });
          }},
         {"RESET TO DEFAULTS", [this] {
        pageRequest("daphne.reset_defaults", nullptr,
                    [this](bool ok, const rapidjson::Value& payload) {
                        if (!ok) {
                            footer()->flash(
                                MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                            return;
                        }
                        const rapidjson::Value& rows {MadJson::getMember(payload, "rows")};
                        if (rows.IsObject()) {
                            for (auto it = rows.MemberBegin(); it != rows.MemberEnd(); ++it)
                                applyRowUpdate(it->value);
                        }
                        relayout();
                        footer()->setStatus(MadJson::getString(payload, "message"));
                    });
          }}});
    endColumn();
    mControlActions.resize(mControls.size(), std::string {}); // Pad non-row controls.
}

void GuiMadPageDaphne::bindAction(const std::string& action)
{
    if (mBinding)
        return;
    mBinding = true;
    const auto row = mRows.find(action);
    const bool isDirection {mSections.count("directions") &&
                            std::find(mSections["directions"].begin(),
                                      mSections["directions"].end(),
                                      action) != mSections["directions"].end()};
    footer()->setStatus((isDirection ? "Push the stick / wheel for" :
                                       "Press the control for") +
                        (" \"" + (row != mRows.end() ? row->second.label : action) +
                         "\" on your X-Arcade… (10s)"));
    pageRequest(
        "daphne.bind",
        [action](MadJson::Writer& writer) {
            writer.Key("action");
            writer.String(action.c_str(), static_cast<rapidjson::SizeType>(action.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            mBinding = false;
            if (!ok) {
                footer()->flash("Bind failed: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            const rapidjson::Value& rows {MadJson::getMember(payload, "rows")};
            bool changed {false};
            if (rows.IsObject()) {
                for (auto it = rows.MemberBegin(); it != rows.MemberEnd(); ++it) {
                    applyRowUpdate(it->value);
                    changed = true;
                }
            }
            if (changed)
                relayout(); // Re-flow the row widths for the new labels.
            footer()->setStatus(MadJson::getString(payload, "message"),
                                MadJson::getBool(payload, "warn"));
        },
        20000); // The capture itself runs up to ~14 s in the daemon.
}

void GuiMadPageDaphne::clearAction(const std::string& action)
{
    pageRequest(
        "daphne.clear",
        [action](MadJson::Writer& writer) {
            writer.Key("action");
            writer.String(action.c_str(), static_cast<rapidjson::SizeType>(action.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            applyRowUpdate(MadJson::getMember(payload, "row"));
            relayout(); // Re-flow the row widths for the new label.
            footer()->setStatus(MadJson::getString(payload, "message"));
        });
}

bool GuiMadPageDaphne::input(InputConfig* config, Input input)
{
    // X clears the focused action row (rows are single buttons: A = bind).
    if (input.value != 0 && config->isMappedTo("x", input) && mBuilt &&
        mFocus < static_cast<int>(mControlActions.size()) &&
        !mControlActions[mFocus].empty()) {
        if (!mBinding) // Never mutate the buffer while a bind is in flight.
            clearAction(mControlActions[mFocus]);
        return true;
    }
    return MadLightgunPageBase::input(config, input);
}

std::vector<HelpPrompt> GuiMadPageDaphne::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {MadLightgunPageBase::getHelpPrompts()};
    if (mBuilt && mFocus < static_cast<int>(mControlActions.size()) &&
        !mControlActions[mFocus].empty()) {
        // Rename the generic "select" and add the clear shortcut for rows.
        for (HelpPrompt& prompt : prompts) {
            if (prompt.first == "a")
                prompt.second = "bind";
        }
        prompts.push_back(HelpPrompt("x", "clear"));
    }
    return prompts;
}
