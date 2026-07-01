//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmuInputMap.h
//
//  MAD control panel: generic per-button input-map page for a standalone
//  emulator (deck-patches). Emulator-agnostic: it loads grouped, mappable
//  actions from "<emu>.input_get", and on an A-press captures a press
//  (identify / axis / pointer modal) and forwards the RAW captured value + kind
//  to "<emu>.input_set" — each emulator's backend translates it to that
//  emulator's own binding token. One page serves PCSX2, RPCS3, Dolphin, Eden, …
//

#ifndef ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_INPUT_MAP_H
#define ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_INPUT_MAP_H

#include "guis/mad/pages/GuiMadPageLightgun.h" // MadLightgunPageBase scaffolding.

#include <string>
#include <utility>
#include <vector>

class GuiMadPageEmuInputMap : public MadLightgunPageBase
{
public:
    // ctxKey/ctxVal (optional) = an extra request param sent on every input_get / input_set /
    // selector_set — e.g. ("titleid", "<SERIAL>_<CRC>") to target a PER-GAME input store through
    // the same backend, exactly like GuiMadPageEmuSettings. Empty (the default) = a normal page.
    GuiMadPageEmuInputMap(GuiMadPanel* panel, const std::string& title, const std::string& emu,
                          const std::string& ctxKey = "", const std::string& ctxVal = "");

    void build() override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void populate(const rapidjson::Value& result);
    // Route an A-press to the capture mode for this action's kind (btn/axis/gun).
    void captureFor(const std::string& id, const std::string& label, const std::string& kind);
    // Forward the raw captured value to <emu>.input_set; gunKind is set only for
    // pointer (lightgun) captures.
    void setBind(const std::string& id, const std::string& kind, const std::string& value,
                 const std::string& gunKind, const std::string& label);
    // Render the optional "selectors" from input_get (controller type, console
    // mode, …) as MadSteppers, each writing back via <emu>.selector_set.
    void addSelectors(const rapidjson::Value& result);
    // Render the optional "actions" from input_get as buttons that fire their own
    // rpc directly (e.g. "Start Sinden guns" -> sinden.driver), like the settings page.
    void addActions(const rapidjson::Value& result);
    // Forward a selector change to <emu>.selector_set (player-scoped selectors
    // carry the current player; global ones omit it). A "dependent" selector
    // rebuilds the page on success, so its value can swap which rows are shown
    // (e.g. a USB port's None/HID Mouse/Light Gun type).
    void setSelector(const std::string& key, const std::string& value, const std::string& label,
                     bool global, bool dependent = false);

    std::string mEmu;     // RPC namespace, e.g. "pcsx2".
    std::string mCtxKey;  // extra request param (e.g. "titleid" for a per-game page); empty = none.
    std::string mCtxVal;
    std::string mPlayer;  // selected player id ("" = backend default); set from input_get.
    // (id, label) of every selectable player — drives the "Player ‹ N ›" stepper
    // for emulators that report more than one (Ryujinx / Eden).
    std::vector<std::pair<std::string, std::string>> mPlayers;
};

#endif // ES_APP_GUIS_MAD_PAGES_GUI_MAD_PAGE_EMU_INPUT_MAP_H
