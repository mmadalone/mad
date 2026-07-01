//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmuInputMap.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageEmuInputMap.h"

#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

#include <algorithm>
#include <cmath>
#include <functional>
#include <string>
#include <utility>
#include <vector>

GuiMadPageEmuInputMap::GuiMadPageEmuInputMap(GuiMadPanel* panel, const std::string& title,
                                             const std::string& emu, const std::string& ctxKey,
                                             const std::string& ctxVal)
    : MadLightgunPageBase {panel, title}
    , mEmu {emu}
    , mCtxKey {ctxKey}
    , mCtxVal {ctxVal}
{
}

void GuiMadPageEmuInputMap::build()
{
    if (!mBuilt) // on a refresh keep the current rows visible until the new ones swap in
        setLoadingText("Loading bindings…");
    const std::string player {mPlayer}; // "" on first load → backend's default player
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    pageRequest(
        mEmu + ".input_get",
        [player, ctxKey, ctxVal](MadJson::Writer& w) {
            w.Key("player");
            w.String(player.c_str(), static_cast<rapidjson::SizeType>(player.length()));
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load input bindings: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            populate(payload);
        },
        8000);
}

void GuiMadPageEmuInputMap::populate(const rapidjson::Value& result)
{
    // Player list + current selection (emulators that support >1 player report these).
    mPlayers.clear();
    const rapidjson::Value& players {MadJson::getMember(result, "players")};
    if (players.IsArray())
        for (const rapidjson::Value& p : players.GetArray())
            mPlayers.emplace_back(MadJson::getString(p, "id"), MadJson::getString(p, "label"));
    mPlayer = MadJson::getString(result, "player", mPlayer);

    beginColumn();
    const float pad {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.3f};

    // Player selector — a "Player ‹ N ›" stepper that re-fetches that player's
    // bindings on change. Only shown when there's more than one player.
    if (mPlayers.size() > 1) {
        const std::vector<std::pair<std::string, std::string>> opts {mPlayers};
        const int last {static_cast<int>(opts.size()) - 1};
        int cur {0};
        for (int i {0}; i <= last; ++i)
            if (opts[static_cast<size_t>(i)].first == mPlayer) { cur = i; break; }
        addStepper(
            "Player", 0.0f, static_cast<float>(last), 1.0f,
            [opts, last](const float v) {
                // Show just "1".."8" (the static "Player" label already says it);
                // non-numbered slots like "Handheld" show their full label.
                const std::string& lbl {
                    opts[static_cast<size_t>(std::clamp(static_cast<int>(std::lround(v)), 0, last))].second};
                return lbl.rfind("Player ", 0) == 0 ? lbl.substr(7) : lbl;
            },
            [this, opts, last](const float v) {
                const std::string id {
                    opts[static_cast<size_t>(std::clamp(static_cast<int>(std::lround(v)), 0, last))].first};
                if (id != mPlayer) {
                    mPlayer = id;
                    build(); // re-fetch this player's bindings
                }
            },
            static_cast<float>(cur), 0.95f, 0.30f);
    }

    addSelectors(result); // controller type, console mode, … (when reported)
    addActions(result);   // one-press action buttons (e.g. Start Sinden guns) when reported

    const std::string note {MadJson::getString(result, "note")};
    if (MadJson::getBool(result, "running", false))
        addBlock("●  " + (note.empty() ? std::string("This emulator is running — close it before "
                                                     "changing bindings (it rewrites its config "
                                                     "on exit).")
                                       : note),
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Red), pad);
    else {
        // Show the backend note when there is one — it names the controller this
        // slot maps ("Controller: …") so the user can see which pad they're editing
        // (it was previously dropped here, the reason the controller felt invisible).
        if (!note.empty())
            addBlock(note, FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), pad);
        addBlock("Pick a row, then press the button you want bound to that action.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary),
                 note.empty() ? pad : 0.0f);
    }

    const rapidjson::Value& groups {MadJson::getMember(result, "groups")};
    if (!groups.IsArray()) {
        endColumn();
        return;
    }
    for (const rapidjson::Value& g : groups.GetArray()) {
        header(MadJson::getString(g, "title"));
        const rapidjson::Value& binds {MadJson::getMember(g, "binds")};
        if (!binds.IsArray())
            continue;
        // Capturable binds go in a wrapping button grid (true 4-way nav); a
        // non-capturable bind (e.g. PCSX2 d-pad/sticks for now) shows read-only.
        std::vector<std::pair<std::string, std::function<void()>>> row;
        for (const rapidjson::Value& b : binds.GetArray()) {
            const std::string id {MadJson::getString(b, "id")};
            const std::string label {MadJson::getString(b, "label", id)};
            const std::string kind {MadJson::getString(b, "kind", "btn")};
            const std::string val {MadJson::getString(b, "value")};
            const std::string shown {val.empty() ? "—" : val};
            if (MadJson::getBool(b, "capturable", false))
                row.emplace_back(label + ": " + shown,
                                 [this, id, label, kind] { captureFor(id, label, kind); });
            else
                addBlock("   " + label + ": " + shown, FONT_SIZE_SMALL,
                         MadTheme::color(MadColor::Secondary), 0.0f);
        }
        if (!row.empty())
            addButtonRow(row, false);
    }
    endColumn();
}

void GuiMadPageEmuInputMap::addSelectors(const rapidjson::Value& result)
{
    const rapidjson::Value& selectors {MadJson::getMember(result, "selectors")};
    if (!selectors.IsArray())
        return;
    for (const rapidjson::Value& s : selectors.GetArray()) {
        const std::string key {MadJson::getString(s, "key")};
        const std::string label {MadJson::getString(s, "label", key)};
        const bool global {MadJson::getString(s, "scope") == "global"};
        std::vector<std::pair<std::string, std::string>> opts; // (value, label)
        const rapidjson::Value& os {MadJson::getMember(s, "options")};
        if (os.IsArray())
            for (const rapidjson::Value& o : os.GetArray())
                opts.emplace_back(MadJson::getString(o, "value"), MadJson::getString(o, "label"));
        if (opts.empty())
            continue;
        const std::string current {MadJson::getString(s, "value")};
        const bool dependent {MadJson::getBool(s, "dependent", false)};
        const int last {static_cast<int>(opts.size()) - 1};
        int cur {0};
        for (int i {0}; i <= last; ++i)
            if (opts[static_cast<size_t>(i)].first == current) { cur = i; break; }
        addStepper(
            label, 0.0f, static_cast<float>(last), 1.0f,
            [opts, last](const float v) {
                return opts[static_cast<size_t>(std::clamp(static_cast<int>(std::lround(v)), 0, last))].second;
            },
            [this, key, label, global, dependent, opts, last](const float v) {
                setSelector(
                    key,
                    opts[static_cast<size_t>(std::clamp(static_cast<int>(std::lround(v)), 0, last))].first,
                    label, global, dependent);
            },
            static_cast<float>(cur), 0.95f, 0.42f);
    }
}

void GuiMadPageEmuInputMap::addActions(const rapidjson::Value& result)
{
    const rapidjson::Value& actions {MadJson::getMember(result, "actions")};
    if (!actions.IsArray())
        return;
    for (const rapidjson::Value& s : actions.GetArray()) {
        const std::string label {MadJson::getString(s, "label")};
        const std::string rpc {MadJson::getString(s, "rpc")};
        if (label.empty() || rpc.empty())
            continue;
        // Snapshot the args object (string values only) into owned strings — the
        // rapidjson Value isn't safe to hold past build(). Same fire pattern as the
        // settings page's addActionButton.
        std::vector<std::pair<std::string, std::string>> args;
        const rapidjson::Value& a {MadJson::getMember(s, "args")};
        if (a.IsObject())
            for (auto it = a.MemberBegin(); it != a.MemberEnd(); ++it)
                if (it->value.IsString())
                    args.emplace_back(
                        it->name.GetString(),
                        std::string {it->value.GetString(), it->value.GetStringLength()});
        addButton(label, [this, rpc, args] {
            pageRequest(
                rpc,
                [args](MadJson::Writer& writer) {
                    for (const std::pair<std::string, std::string>& kv : args) {
                        writer.Key(kv.first.c_str());
                        writer.String(kv.second.c_str(),
                                      static_cast<rapidjson::SizeType>(kv.second.length()));
                    }
                },
                [this](bool ok, const rapidjson::Value& payload) {
                    footer()->setStatus("");
                    footer()->flash(MadJson::getString(payload, "message", "unknown error"), 5000,
                                    !ok);
                },
                10000);
        });
    }
}

void GuiMadPageEmuInputMap::setSelector(const std::string& key, const std::string& value,
                                        const std::string& label, const bool global,
                                        const bool dependent)
{
    const std::string player {mPlayer};
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    pageRequest(
        mEmu + ".selector_set",
        [key, value, player, global, ctxKey, ctxVal](MadJson::Writer& w) {
            w.Key("key");
            w.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            w.Key("value");
            w.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
            if (!global && !player.empty()) {
                w.Key("player");
                w.String(player.c_str(), static_cast<rapidjson::SizeType>(player.length()));
            }
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
        },
        [this, label, dependent](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't set " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            footer()->flash("Set " + label, 2500, false);
            // A dependent selector decides which rows the page shows, so re-fetch
            // them now that the new value is committed (e.g. USB Type -> its binds).
            if (dependent)
                build();
        });
}

void GuiMadPageEmuInputMap::captureFor(const std::string& id, const std::string& label,
                                       const std::string& kind)
{
    std::weak_ptr<int> alive {pageAlive()};
    if (kind == "axis") {
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "axisname", "Move the stick for " + label + "…",
            [this, alive, id, label](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr || r->axisToken.empty())
                    return;
                setBind(id, "axis", r->axisToken, "", label);
            }));
    }
    else if (kind == "gun") {
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "pointer", "Press a button or key for " + label + "…",
            [this, alive, id, label](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr || r->gunKind.empty())
                    return;
                setBind(id, "gun", r->gunValue, r->gunKind, label);
            }));
    }
    else {
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "identify", "Press a button or d-pad direction for " + label + "…",
            [this, alive, id, label, kind](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr)
                    return;
                // The X-Arcade arcade stick DUAL-EMITS: a button (held / btn_indices, what
                // RetroArch reads) AND a d-pad hat token (bindToken, what the SDL standalones
                // read). On a D-PAD row (kind=="hat") prefer the hat token, so the stick rides the
                // existing kind=="hat" writers — every emu maps a d-pad via a hat token, never a
                // raw button code. On a face-button row the button still wins.
                const bool happyHeld {!r->held.empty() && r->held[0] >= 0x2c0 &&
                                      r->held[0] <= 0x2c3};
                if (kind == "hat" && mEmu == "eden" && happyHeld) {
                    // Eden alone reads the stick by raw SDL-joystick rank, so its d-pad token map
                    // would mis-bind left/right. Refuse rather than silently bind the wrong way.
                    footer()->flash("Eden + X-Arcade d-pad isn't supported yet — use RetroArch "
                                    "for arcade-stick d-pad.",
                                    5000, true);
                    return;
                }
                if (kind == "hat" && !r->bindToken.empty())
                    // D-pad row: the hat token (e.g. "h0up"); the backend maps it to this
                    // emulator's d-pad token. The X-Arcade stick arrives here via dual-emit.
                    setBind(id, "hat", r->bindToken, "", label);
                else if (!r->held.empty())
                    // A button (face/shoulder/…); forward the RAW evdev code, the backend
                    // maps it to this emulator's binding token.
                    setBind(id, "btn", std::to_string(r->held[0]), "", label);
                else if (!r->bindToken.empty())
                    // A genuine d-pad press that arrived on a non-hat row (or any leftover token).
                    setBind(id, "hat", r->bindToken, "", label);
            }));
    }
}

void GuiMadPageEmuInputMap::setBind(const std::string& id, const std::string& kind,
                                    const std::string& value, const std::string& gunKind,
                                    const std::string& label)
{
    const std::string player {mPlayer};
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    pageRequest(
        mEmu + ".input_set",
        [id, kind, value, gunKind, player, ctxKey, ctxVal](MadJson::Writer& w) {
            w.Key("id");
            w.String(id.c_str(), static_cast<rapidjson::SizeType>(id.length()));
            w.Key("kind");
            w.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
            w.Key("value");
            w.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
            if (!gunKind.empty()) {
                w.Key("gun_kind");
                w.String(gunKind.c_str(), static_cast<rapidjson::SizeType>(gunKind.length()));
            }
            if (!player.empty()) {
                w.Key("player");
                w.String(player.c_str(), static_cast<rapidjson::SizeType>(player.length()));
            }
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
        },
        [this, label](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't set " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            footer()->flash("Set " + label, 2500, false);
            build(); // refresh the shown values
        });
}

std::vector<HelpPrompt> GuiMadPageEmuInputMap::getHelpPrompts()
{
    return {HelpPrompt("up/down/left/right", "choose"), HelpPrompt("a", "rebind"),
            HelpPrompt("b", "back")};
}
