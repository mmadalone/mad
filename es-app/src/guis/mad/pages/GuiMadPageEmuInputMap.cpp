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
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (the shared long-option picker)

#include <algorithm>
#include <cmath>
#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

GuiMadPageEmuInputMap::GuiMadPageEmuInputMap(GuiMadPanel* panel, const std::string& title,
                                             const std::string& emu, const std::string& ctxKey,
                                             const std::string& ctxVal, const std::string& context)
    : MadLightgunPageBase {panel, title}
    , mEmu {emu}
    , mCtxKey {ctxKey}
    , mCtxVal {ctxVal}
    , mContext {context}
{
}

void GuiMadPageEmuInputMap::build()
{
    if (!mBuilt) // on a refresh keep the current rows visible until the new ones swap in
        setLoadingText("Loading bindings…");
    const std::string player {mPlayer}; // "" on first load → backend's default player
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string context {mContext};
    pageRequest(
        mEmu + ".input_get",
        [player, ctxKey, ctxVal, context](MadJson::Writer& w) {
            w.Key("player");
            w.String(player.c_str(), static_cast<rapidjson::SizeType>(player.length()));
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!context.empty()) {
                w.Key("context");
                w.String(context.c_str(), static_cast<rapidjson::SizeType>(context.length()));
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
    mClearable = MadJson::getBool(result, "clearable", false);
    mBuffered = MadJson::getBool(result, "buffered", false);
    mDirty = MadJson::getBool(result, "dirty", false);
    mBindByComp.clear();

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
        std::vector<BindRef> rowRefs; // 1:1 with `row` — for Start-to-clear
        for (const rapidjson::Value& b : binds.GetArray()) {
            const std::string id {MadJson::getString(b, "id")};
            const std::string label {MadJson::getString(b, "label", id)};
            const std::string kind {MadJson::getString(b, "kind", "btn")};
            const std::string val {MadJson::getString(b, "value")};
            const std::string shown {val.empty() ? "—" : val};
            if (MadJson::getBool(b, "capturable", false)) {
                row.emplace_back(label + ": " + shown,
                                 [this, id, label, kind] { captureFor(id, label, kind); });
                rowRefs.push_back({id, kind, label});
            }
            else
                addBlock("   " + label + ": " + shown, FONT_SIZE_SMALL,
                         MadTheme::color(MadColor::Secondary), 0.0f);
        }
        if (!row.empty()) {
            const auto buttons {addButtonRow(row, false)};
            // Map each bind button → its (id,kind,label) so a Start-press on the focused
            // button clears it (1:1 with rowRefs by construction).
            for (size_t i {0}; i < buttons.size() && i < rowRefs.size(); ++i)
                mBindByComp[buttons[i].get()] = rowRefs[i];
        }
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
        auto stepper = addStepper(
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
        // A opens the full scrollable list, mirroring GuiMadPageEmuSettings::addEnumStepper, so these
        // selectors (controller type, console mode) get the same picker as every other enum row. opts
        // is already {value, display}; setValue updates the row in place, setSelector writes.
        std::weak_ptr<MadStepper> weak {stepper};
        stepper->setOnActivate([this, key, label, global, dependent, opts, last, weak] {
            auto s {weak.lock()};
            if (s == nullptr)
                return;
            const int curv {std::clamp(static_cast<int>(std::lround(s->value())), 0, last)};
            mPanel->pushPage(new GuiMadPageBackendChoice(
                mPanel, label, "", opts, opts[static_cast<size_t>(curv)].first,
                [this, key, label, global, dependent, opts, last, weak](const std::string& value) {
                    int i {0};
                    for (int j {0}; j <= last; ++j)
                        if (opts[static_cast<size_t>(j)].first == value) {
                            i = j;
                            break;
                        }
                    if (auto sp {weak.lock()})
                        sp->setValue(static_cast<float>(i));
                    setSelector(key, opts[static_cast<size_t>(i)].first, label, global, dependent);
                }));
        });
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
    const std::string context {mContext};
    pageRequest(
        mEmu + ".selector_set",
        [key, value, player, global, ctxKey, ctxVal, context](MadJson::Writer& w) {
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
            if (!context.empty()) {
                w.Key("context");
                w.String(context.c_str(), static_cast<rapidjson::SizeType>(context.length()));
            }
        },
        [this, label, dependent](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't set " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            if (mBuffered) {
                mDirty = MadJson::getBool(p, "dirty", mDirty);
                mPanel->refreshHelpPrompts();
            }
            footer()->flash("Set " + label + (mBuffered ? " (press X to save)" : ""), 2500, false);
            // A dependent selector decides which rows the page shows, so re-fetch
            // them now that the new value is staged (e.g. USB Type -> its binds).
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
    else if (kind == "trigger") {
        // ZL/ZR on a pad whose triggers are analog axes (DualSense / DS4 / Deck): capture the
        // trigger axis (axisname mode emits e.g. "+trigger_left@4"); the backend writes it as an
        // axis-with-threshold binding. A button-trigger pad (Wii U Pro) uses kind "btn" instead.
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "axisname", "Pull the trigger for " + label + "…",
            [this, alive, id, label](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr || r->axisToken.empty())
                    return;
                setBind(id, "trigger", r->axisToken, "", label);
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
    else if (kind == "chord") {
        // Hotkeys: accumulate ANY simultaneously-held inputs (2+ keys, a pad chord, a trigger,
        // Guide) via combo mode and forward the WHOLE held set. A single press is a 1-element
        // chord, so this also covers single-key / single-button binds.
        mWindow->pushGui(new GuiMadCaptureModal(
            mPanel, "combo", "Hold the key(s) or button(s) for " + label + ", then release…",
            [this, alive, id, label](const GuiMadCaptureModal::Result* r) {
                if (alive.expired() || r == nullptr)
                    return;
                // held is empty when only a d-pad direction was pressed (it arrives as a hat, which
                // has no key code and can't be a hotkey token) — tell the user instead of a silent
                // no-op. (A button+d-pad chord binds just the button; d-pad hotkeys are unsupported.)
                if (r->held.empty()) {
                    footer()->flash("That can't be a hotkey — d-pad directions aren't supported here.",
                                    4000, true);
                    return;
                }
                setChord(id, r->held, r->deviceName, label);
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
    const std::string context {mContext};
    pageRequest(
        mEmu + ".input_set",
        [id, kind, value, gunKind, player, ctxKey, ctxVal, context](MadJson::Writer& w) {
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
            if (!context.empty()) {
                w.Key("context");
                w.String(context.c_str(), static_cast<rapidjson::SizeType>(context.length()));
            }
        },
        [this, label](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't set " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            if (mBuffered) {
                mDirty = MadJson::getBool(p, "dirty", true);
                mPanel->refreshHelpPrompts(); // surface X=save / Y=cancel now
            }
            footer()->flash("Set " + label + (mBuffered ? " (press X to save)" : ""), 2500, false);
            build(); // refresh the shown (staged) values
        });
}

void GuiMadPageEmuInputMap::setChord(const std::string& id, const std::vector<int>& held,
                                     const std::string& deviceName, const std::string& label)
{
    const std::string player {mPlayer};
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string context {mContext};
    const std::vector<int> codes {held};
    const std::string device {deviceName};   // forwarded so a backend can device-qualify (dolphin_hk)
    pageRequest(
        mEmu + ".input_set",
        [id, codes, device, player, ctxKey, ctxVal, context](MadJson::Writer& w) {
            w.Key("id");
            w.String(id.c_str(), static_cast<rapidjson::SizeType>(id.length()));
            w.Key("kind");
            w.String("chord", 5);
            w.Key("codes");
            w.StartArray();
            for (const int c : codes)
                w.Int(c);
            w.EndArray();
            if (!device.empty()) {
                w.Key("device");
                w.String(device.c_str(), static_cast<rapidjson::SizeType>(device.length()));
            }
            if (!player.empty()) {
                w.Key("player");
                w.String(player.c_str(), static_cast<rapidjson::SizeType>(player.length()));
            }
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!context.empty()) {
                w.Key("context");
                w.String(context.c_str(), static_cast<rapidjson::SizeType>(context.length()));
            }
        },
        [this, label](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't set " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            if (mBuffered) {
                mDirty = MadJson::getBool(p, "dirty", true);
                mPanel->refreshHelpPrompts(); // surface X=save / Y=cancel now
            }
            footer()->flash("Set " + label + (mBuffered ? " (press X to save)" : ""), 2500, false);
            build(); // refresh the shown (staged) values
        });
}

void GuiMadPageEmuInputMap::clearBind(const std::string& id, const std::string& kind,
                                      const std::string& label)
{
    const std::string player {mPlayer};
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string context {mContext};
    pageRequest(
        mEmu + ".input_clear",
        [id, kind, player, ctxKey, ctxVal, context](MadJson::Writer& w) {
            w.Key("id");
            w.String(id.c_str(), static_cast<rapidjson::SizeType>(id.length()));
            w.Key("kind");
            w.String(kind.c_str(), static_cast<rapidjson::SizeType>(kind.length()));
            if (!player.empty()) {
                w.Key("player");
                w.String(player.c_str(), static_cast<rapidjson::SizeType>(player.length()));
            }
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!context.empty()) {
                w.Key("context");
                w.String(context.c_str(), static_cast<rapidjson::SizeType>(context.length()));
            }
        },
        [this, label](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash("Couldn't clear " + label + ": " +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            if (mBuffered) {
                mDirty = MadJson::getBool(p, "dirty", true);
                mPanel->refreshHelpPrompts();
            }
            footer()->flash("Cleared " + label + (mBuffered ? " (press X to save)" : ""), 2500, false);
            build(); // refresh the shown (staged) values
        });
}

bool GuiMadPageEmuInputMap::input(InputConfig* config, Input input)
{
    // Clear a binding: with a bind row FOCUSED, press Start (no capture modal opens). Gated on
    // the backend advertising "clearable" — Start stays fully bindable INSIDE the capture modal
    // (entered with A), so the two never collide.
    if (input.value != 0 && mClearable && config->isMappedTo("start", input) && mFocus >= 0 &&
        mFocus < static_cast<int>(mControls.size())) {
        const auto it {mBindByComp.find(mControls[mFocus].comp)};
        if (it != mBindByComp.end()) {
            clearBind(it->second.id, it->second.kind, it->second.label);
            return true;
        }
    }
    return MadLightgunPageBase::input(config, input);
}

std::vector<HelpPrompt> GuiMadPageEmuInputMap::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {HelpPrompt("up/down/left/right", "choose"),
                                     HelpPrompt("a", "rebind")};
    if (mClearable)
        prompts.emplace_back("start", "clear");
    if (mBuffered && mDirty) {
        prompts.emplace_back("x", "save");
        prompts.emplace_back("y", "cancel");
    }
    prompts.emplace_back("b", "back");
    return prompts;
}

bool GuiMadPageEmuInputMap::madSave()
{
    if (!mBuffered || !mDirty)
        return false; // non-buffered / clean: let X fall through to input()
    requestSaveCancel(mEmu + ".input_save");
    return true;
}

bool GuiMadPageEmuInputMap::madCancel()
{
    if (!mBuffered || !mDirty)
        return false;
    requestSaveCancel(mEmu + ".input_cancel");
    return true;
}

void GuiMadPageEmuInputMap::requestSaveCancel(const std::string& method)
{
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string context {mContext};
    const bool save {method.rfind(".input_save") != std::string::npos};
    pageRequest(
        method,
        [ctxKey, ctxVal, context](MadJson::Writer& w) {
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!context.empty()) {
                w.Key("context");
                w.String(context.c_str(), static_cast<rapidjson::SizeType>(context.length()));
            }
        },
        [this, save](bool ok, const rapidjson::Value& p) {
            if (!ok) {
                footer()->flash(std::string {save ? "Couldn't save: " : "Couldn't cancel: "} +
                                    MadJson::getString(p, "message", "error"),
                                4000, true);
                return;
            }
            mDirty = MadJson::getBool(p, "dirty", false);
            footer()->flash(save ? "Saved." : "Reverted to saved.", 2500, false);
            build(); // re-fetch: saved values persist, cancelled values revert; refreshes prompts
        });
}
