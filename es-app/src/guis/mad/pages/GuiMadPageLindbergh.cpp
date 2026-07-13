//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLindbergh.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageLindbergh.h"

#include "guis/mad/GuiMadCaptureModal.h" // reused for the per-game quit-combo capture.
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadBackend.h"   // setEventCallback for the live "lindbergh.fired" readout
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (game picker).

#include <algorithm>

namespace
{
    // The picked game persists across page entries within a session.
    std::string sTitleId;
} // namespace

GuiMadPageLindbergh::GuiMadPageLindbergh(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "LINDBERGH INPUT MAPPING"}
{
}

GuiMadPageLindbergh::GuiMadPageLindbergh(GuiMadPanel* panel, const std::string& title,
                                         const std::string& titleid)
    : MadLightgunPageBase {panel, title}
    , mInitialTitleId {titleid}
    , mPrepicked {true}
{
}

void GuiMadPageLindbergh::build()
{
    // Live readout: the backend monitor pushes "lindbergh.fired" per press while gun
    // capture mode is on; show the last-fired token (but never over an active bind prompt).
    std::weak_ptr<int> alive {pageAlive()};
    mPanel->getBackend()->setEventCallback(
        "lindbergh.fired", [this, alive](const rapidjson::Value& data) {
            if (alive.expired() || mBinding)
                return;
            const std::string tok {MadJson::getString(data, "token")};
            if (!tok.empty())
                footer()->setStatus("Gun capture live — last fired:  " + tok);
        });
    setLoadingText("Loading…");
    // Game-first entry loads the pre-picked title; the standalone binder resumes the session's
    // last-picked game (sTitleId), or shows the inline picker when none is set.
    load(mPrepicked ? mInitialTitleId : sTitleId);
}

void GuiMadPageLindbergh::load(const std::string& titleid, bool announce)
{
    pageRequest(
        "lindbergh.load",
        [titleid](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(titleid.c_str(), static_cast<rapidjson::SizeType>(titleid.length()));
        },
        [this, titleid, announce](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                // A stale titleid (game removed): fall back to the picker.
                if (!titleid.empty()) {
                    sTitleId.clear();
                    load("");
                    return;
                }
                footer()->setStatus("Couldn't load: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            sTitleId = titleid;
            parse(payload);
            relayout();
            if (announce && !mGameName.empty())
                footer()->flash("Mapping " + mGameName, 4000);
        },
        10000);
}

void GuiMadPageLindbergh::parse(const rapidjson::Value& result)
{
    mTitleId = MadJson::getString(result, "titleid");
    mGameName = MadJson::getString(result, "game_name");
    mCaption = MadJson::getString(result, "caption");
    mGun = MadJson::getBool(result, "gun");
    mDirty = MadJson::getBool(result, "dirty", false);

    // Per-game hold-to-quit combo (display only; capture/clear go through the existing policy RPCs).
    mQuitScope = mTitleId.empty() ? std::string {} : "lindbergh-" + mTitleId;
    mQuitDisplay.clear();
    const rapidjson::Value& quitCombo {MadJson::getMember(result, "quit_combo")};
    if (quitCombo.IsObject()) {
        mQuitScope = MadJson::getString(quitCombo, "scope", mQuitScope);
        mQuitDisplay = MadJson::getString(quitCombo, "display");
    }

    mRows.clear();
    const rapidjson::Value& rows {MadJson::getMember(result, "rows")};
    if (rows.IsObject()) {
        for (auto it = rows.MemberBegin(); it != rows.MemberEnd(); ++it) {
            Row row;
            row.key = MadJson::getString(it->value, "key");
            row.label = MadJson::getString(it->value, "label", row.key);
            row.display = MadJson::getString(it->value, "display");
            row.warn = MadJson::getBool(it->value, "warn");
            row.axis = MadJson::getBool(it->value, "axis");
            mRows[row.key] = row;
        }
    }

    mSections.clear();
    const rapidjson::Value& sections {MadJson::getMember(result, "sections")};
    if (sections.IsObject()) {
        for (auto it = sections.MemberBegin(); it != sections.MemberEnd(); ++it) {
            std::vector<std::string> keys;
            if (it->value.IsArray())
                for (rapidjson::SizeType i {0}; i < it->value.Size(); ++i)
                    if (it->value[i].IsString())
                        keys.emplace_back(it->value[i].GetString());
            mSections[it->name.GetString()] = keys;
        }
    }

    mGames.clear();
    const rapidjson::Value& games {MadJson::getMember(result, "games")};
    if (games.IsArray())
        for (rapidjson::SizeType i {0}; i < games.Size(); ++i)
            mGames.push_back({MadJson::getString(games[i], "titleid"),
                              MadJson::getString(games[i], "name")});
}

std::string GuiMadPageLindbergh::rowText(const Row& row) const
{
    return row.label + " — " + row.display + (row.warn ? "  ⚠" : "");
}

void GuiMadPageLindbergh::applyRowUpdate(const rapidjson::Value& row)
{
    Row updated;
    updated.key = MadJson::getString(row, "key");
    if (updated.key.empty())
        return;
    updated.label = MadJson::getString(row, "label", updated.key);
    updated.display = MadJson::getString(row, "display");
    updated.warn = MadJson::getBool(row, "warn");
    updated.axis = MadJson::getBool(row, "axis");
    mRows[updated.key] = updated;
}

void GuiMadPageLindbergh::relayout()
{
    mControlActions.clear();
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    auto addActionRows = [this](const std::vector<std::string>& keys) {
        std::vector<std::pair<std::string, std::function<void()>>> items;
        std::vector<std::string> order;
        for (const std::string& key : keys) {
            const auto it = mRows.find(key);
            if (it == mRows.end())
                continue;
            items.emplace_back(rowText(it->second), [this, key] { bindAction(key); });
            order.emplace_back(key);
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

    addBlock("Pick a game, focus a control, press A to bind, then actuate it (press a button, "
             "or move a wheel/pedal/stick). Start clears a row. X saves; Y cancels.",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Primary), smallHeight * 0.4f);

    if (mTitleId.empty()) {
        // Show the game list INLINE (one button per game) so it appears immediately on entry —
        // no separate "PICK A GAME" chooser step. Each button loads that game's controls.
        header("Pick a game");
        if (mGames.empty()) {
            addBlock("No Lindbergh games found under ~/ROMs/lindbergh.", FONT_SIZE_SMALL,
                     MadTheme::color(MadColor::Secondary), smallHeight * 0.3f);
        }
        else {
            for (size_t i {0}; i < mGames.size(); ++i) {
                const std::string tid {mGames[i].titleid};
                const std::string name {mGames[i].name};
                addButton(name, [this, tid, name] {
                    setLoadingText("Loading " + name + "…");
                    load(tid, true);
                });
            }
        }
        endColumn();
        mControlActions.resize(mControls.size(), std::string {}); // game buttons aren't bindable rows
        return;
    }

    if (mPrepicked) {
        // The game was chosen upstream (game-first menu): confirm it in a header, but offer no
        // "change game" (that would re-open a picker inside a page reached BY picking a game).
        header(mGameName);
    }
    else {
        header("Game");
        addButton("▸ " + mGameName + "   (change game)", [this] {
            sTitleId.clear();
            load(""); // back to the inline game list
        });
    }

    if (!mCaption.empty())
        caption(mCaption);

    if (mGun) {
        header("Gun capture mode");
        caption("Start the gun pipeline so the Sinden guns are live, TEST FIRE to confirm, then "
                "bind below. Stop it when you're done.");
        addButtonRow({{"START GUN", [this] { gunDriver("start"); }},
                      {"STOP GUN", [this] { gunDriver("stop"); }}});
        addButton("TEST FIRE — pull the trigger to check", [this] { testFire(); });
    }

    if (!mSections["p1"].empty()) {
        header(mSections.count("p2") && !mSections["p2"].empty() ? "Player 1" : "Controls");
        addActionRows(mSections["p1"]);
    }
    if (!mSections["p2"].empty()) {
        header("Player 2");
        addActionRows(mSections["p2"]);
    }
    if (!mSections["axes"].empty()) {
        header("Axes (wheel / pedals / stick)");
        caption("Bind by MOVING the control — the analog channel is captured, not a press.");
        addActionRows(mSections["axes"]);
    }
    if (!mSections["system"].empty()) {
        header("System (test / service)");
        addActionRows(mSections["system"]);
    }

    // Per-game hold-to-quit combo. Reuses the global quit-combo capture (mouse buttons included, for
    // guns) + watcher; applies IMMEDIATELY (separate from the buffered binds, which save via X).
    header("Quit combo");
    caption(mGun
                ? "Hold-to-quit combo for THIS game. Start GUN capture above first, then capture it on "
                  "the GUN (mouse buttons work). Avoid a button you hold during play (e.g. the trigger)."
                : "Hold-to-quit combo for THIS game. Capture it on the pad / X-Arcade. Avoid a button "
                  "you hold during play. Applies immediately.");
    addBlock(std::string("Current:  ") +
                 (mQuitDisplay.empty() ? "— not set (uses the default combo)" : mQuitDisplay),
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), smallHeight * 0.3f);
    addButtonRow({{"SET QUIT COMBO", [this] { captureQuitCombo(); }},
                  {"CLEAR QUIT COMBO", [this] { clearQuitCombo(); }}});

    endColumn();
    mControlActions.resize(mControls.size(), std::string {});
}

void GuiMadPageLindbergh::saveOrCancel(const char* method)
{
    const std::string titleid {mTitleId};
    const bool isCancel {std::string {method} == "lindbergh.cancel"};
    pageRequest(
        method,
        [titleid](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(titleid.c_str(), static_cast<rapidjson::SizeType>(titleid.length()));
        },
        [this, isCancel, titleid](bool ok, const rapidjson::Value& payload) {
            footer()->setStatus("");
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "unknown error"), 4000, true);
                return;
            }
            if (isCancel) {
                load(titleid); // reload the reverted bindings (reseeds mDirty from the fresh payload)
                footer()->flash(MadJson::getString(payload, "message", "Reverted."), 4000);
                return;
            }
            mDirty = false;
            footer()->flash(MadJson::getString(payload, "message", "Saved."), 5000);
        },
        8000);
}

void GuiMadPageLindbergh::gunDriver(const char* action)
{
    const std::string act {action};
    pageRequest(
        "sinden.driver",
        [act](MadJson::Writer& writer) {
            writer.Key("action");
            writer.String(act.c_str(), static_cast<rapidjson::SizeType>(act.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            footer()->flash(MadJson::getString(payload, "message", ok ? "Done." : "Failed"), 4000,
                            !ok);
        },
        8000);
    // Tie the live-readout monitor to capture mode (start it with the pipeline, stop with it).
    const std::string mon {act == "start" ? "lindbergh.monitor_start" : "lindbergh.monitor_stop"};
    const bool starting {act == "start"};
    pageRequest(
        mon, [](MadJson::Writer&) {},
        [this, starting](bool, const rapidjson::Value&) {
            footer()->setStatus(starting ? "Gun capture live — fire to test" : "");
        },
        8000);
}

void GuiMadPageLindbergh::testFire()
{
    footer()->setStatus("Pull the trigger / press a control now… (8s)");
    pageRequest(
        "lindbergh.test_fire", [](MadJson::Writer&) {},
        [this](bool ok, const rapidjson::Value& payload) {
            footer()->setStatus("");
            footer()->flash(MadJson::getString(payload, "message", "unknown error"), 5000,
                            !ok || MadJson::getBool(payload, "warn"));
        },
        14000);
}

void GuiMadPageLindbergh::captureQuitCombo()
{
    if (mQuitScope.empty())
        return; // no game picked
    std::weak_ptr<int> alive {pageAlive()};
    const std::string scope {mQuitScope};
    // "combo" mode opens gamepad + mouse + keyboard nodes and accepts mouse buttons (the gun),
    // face buttons, keyboard keys and the arcade stick — the exact capture the global quit combo uses.
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "combo", "Hold the quit combo on this game's controller/gun, then release…",
        [this, alive, scope](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr)
                return; // cancel / timeout: the modal already gave feedback
            if (result->held.empty()) {
                footer()->flash("Nothing usable captured — hold a button / key / mouse combo "
                                "(a d-pad direction alone can't be a quit combo).",
                                4000, true);
                return;
            }
            const std::vector<int> buttons {result->held};
            std::string disp;
            for (const std::string& n : result->names)
                disp += (disp.empty() ? "" : " + ") + n;
            pageRequest(
                "policy.set_quit_combo",
                [scope, buttons](MadJson::Writer& writer) {
                    writer.Key("scope");
                    writer.String(scope.c_str(), static_cast<rapidjson::SizeType>(scope.length()));
                    writer.Key("buttons");
                    writer.StartArray();
                    for (const int button : buttons)
                        writer.Int(button);
                    writer.EndArray();
                },
                [this, disp](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        footer()->flash("Couldn't set the quit combo: " +
                                            MadJson::getString(payload, "message", "unknown error"),
                                        4000, true);
                        return;
                    }
                    // Refresh the "Current:" line locally — do NOT reload the ini buffer (that would
                    // discard unsaved binding edits); the combo lives in policy, not the ini.
                    mQuitDisplay = disp;
                    relayout();
                    footer()->flash("Quit combo set for this game.", 4000);
                });
        }));
}

void GuiMadPageLindbergh::clearQuitCombo()
{
    if (mQuitScope.empty())
        return;
    const std::string scope {mQuitScope};
    pageRequest(
        "policy.clear_quit_combo",
        [scope](MadJson::Writer& writer) {
            writer.Key("system");
            writer.String(scope.c_str(), static_cast<rapidjson::SizeType>(scope.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "unknown error"), 4000, true);
                return;
            }
            // Local refresh only — never reload the ini buffer here (would drop unsaved bind edits).
            mQuitDisplay.clear();
            relayout();
            footer()->flash("Quit combo cleared — this game uses the default.", 4000);
        });
}

void GuiMadPageLindbergh::bindAction(const std::string& key)
{
    if (mBinding)
        return;
    mBinding = true;
    const auto it = mRows.find(key);
    const std::string label {it != mRows.end() ? it->second.label : key};
    const bool axis {it != mRows.end() && it->second.axis};
    footer()->setStatus((axis ? "MOVE the control for \"" : "Press the control for \"") + label +
                        "\" now… (10s)");
    pageRequest(
        "lindbergh.bind",
        [key, label, axis](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(sTitleId.c_str(), static_cast<rapidjson::SizeType>(sTitleId.length()));
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("label");
            writer.String(label.c_str(), static_cast<rapidjson::SizeType>(label.length()));
            writer.Key("axis");
            writer.Bool(axis);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            mBinding = false;
            footer()->setStatus("");
            if (!ok) {
                footer()->flash("Bind failed: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            const rapidjson::Value& rows {MadJson::getMember(payload, "rows")};
            bool changed {false};
            if (rows.IsObject())
                for (auto r = rows.MemberBegin(); r != rows.MemberEnd(); ++r) {
                    applyRowUpdate(r->value);
                    changed = true;
                }
            mDirty = MadJson::getBool(payload, "dirty", true);
            if (changed)
                relayout();
            footer()->flash(MadJson::getString(payload, "message"), 5000,
                            MadJson::getBool(payload, "warn"));
        },
        20000); // capture runs up to ~14 s in the daemon.
}

void GuiMadPageLindbergh::clearAction(const std::string& key)
{
    const auto it = mRows.find(key);
    const std::string label {it != mRows.end() ? it->second.label : key};
    const bool axis {it != mRows.end() && it->second.axis};
    pageRequest(
        "lindbergh.clear",
        [key, label, axis](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(sTitleId.c_str(), static_cast<rapidjson::SizeType>(sTitleId.length()));
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("label");
            writer.String(label.c_str(), static_cast<rapidjson::SizeType>(label.length()));
            writer.Key("axis");
            writer.Bool(axis);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "unknown error"), 4000, true);
                return;
            }
            applyRowUpdate(MadJson::getMember(payload, "row"));
            mDirty = MadJson::getBool(payload, "dirty", true);
            relayout();
            footer()->setStatus("");
            footer()->flash(MadJson::getString(payload, "message"), 4000);
        });
}

bool GuiMadPageLindbergh::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("start", input) && mBuilt &&
        mFocus < static_cast<int>(mControlActions.size()) && !mControlActions[mFocus].empty()) {
        if (!mBinding)
            clearAction(mControlActions[mFocus]);
        return true;
    }
    return MadLightgunPageBase::input(config, input);
}

std::vector<HelpPrompt> GuiMadPageLindbergh::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {MadLightgunPageBase::getHelpPrompts()};
    if (mBuilt && mFocus < static_cast<int>(mControlActions.size()) &&
        !mControlActions[mFocus].empty()) {
        for (HelpPrompt& prompt : prompts)
            if (prompt.first == "a")
                prompt.second = "bind";
        prompts.push_back(HelpPrompt("start", "clear"));
    }
    if (!mTitleId.empty() && mDirty) {
        prompts.push_back(HelpPrompt("x", "save"));
        prompts.push_back(HelpPrompt("y", "cancel"));
    }
    return prompts;
}

bool GuiMadPageLindbergh::madSave()
{
    // Also guard on !mBinding, mirroring the clear path: a face button actuated in
    // the brief window before the daemon's input.lock arrives must not race an
    // in-flight bind RPC (the capture itself is already blocked by the panel).
    if (mTitleId.empty() || !mDirty || mBinding)
        return false;
    saveOrCancel("lindbergh.save");
    return true;
}

bool GuiMadPageLindbergh::madCancel()
{
    if (mTitleId.empty() || !mDirty || mBinding)
        return false;
    saveOrCancel("lindbergh.cancel");
    return true;
}
