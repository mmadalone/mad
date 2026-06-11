//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePlayers.cpp
//
//  MAD control panel: Players section (deck-patches).
//

#include "guis/mad/pages/GuiMadPagePlayers.h"

#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "utils/StringUtil.h"

#include <algorithm>
#include <cstdlib>

namespace
{
    // The scope's pin table from a merged policy: [pins] or [systems.<s>.pins].
    std::map<int, std::string> parsePinsForScope(const rapidjson::Value& merged,
                                                 const std::string& scope)
    {
        const rapidjson::Value& pins {
            scope.empty() ?
                MadJson::getMember(merged, "pins") :
                MadJson::getMember(MadJson::getMember(MadJson::getMember(merged, "systems"),
                                                      scope.c_str()),
                                   "pins")};
        std::map<int, std::string> result;
        if (pins.IsObject()) {
            for (auto it = pins.MemberBegin(); it != pins.MemberEnd(); ++it) {
                const int player {std::atoi(it->name.GetString())};
                if (player >= 1 && player <= MadPlayerSlots::PLAYER_COUNT &&
                    it->value.IsString())
                    result[player] = it->value.GetString();
            }
        }
        return result;
    }

    // Real gamepads only (the lib.devices joypads() filter).
    std::vector<MadPlayerSlots::Device> devicesFromArray(const rapidjson::Value& devices)
    {
        std::vector<MadPlayerSlots::Device> result;
        if (!devices.IsArray())
            return result;
        for (rapidjson::SizeType i {0}; i < devices.Size(); ++i) {
            const rapidjson::Value& device {devices[i]};
            if (!MadJson::getBool(device, "is_joypad") ||
                MadJson::getBool(device, "is_sinden") ||
                MadJson::getBool(device, "is_steam_virtual"))
                continue;
            result.emplace_back(MadPlayerSlots::Device {MadJson::getString(device, "name"),
                                                        MadJson::getString(device, "pin_id")});
        }
        return result;
    }

    // The Tk _pins_summary: "P1,2,4" from the pinned player numbers.
    std::string pinsSummary(const rapidjson::Value& pins)
    {
        std::vector<int> players;
        if (pins.IsObject()) {
            for (auto it = pins.MemberBegin(); it != pins.MemberEnd(); ++it) {
                const int player {std::atoi(it->name.GetString())};
                if (player > 0)
                    players.emplace_back(player);
            }
        }
        if (players.empty())
            return "(none)";
        std::sort(players.begin(), players.end());
        std::string out {"P"};
        for (size_t i {0}; i < players.size(); ++i) {
            if (i > 0)
                out.append(",");
            out.append(std::to_string(players[i]));
        }
        return out;
    }
} // namespace

//  ── MadPinEditorBase ──

MadPinEditorBase::MadPinEditorBase(GuiMadPanel* panel,
                                   const std::string& title,
                                   const std::string& scope)
    : MadPage {panel, title}
    , mScope {scope}
{
}

void MadPinEditorBase::createSlots()
{
    mSlots = std::make_shared<MadPlayerSlots>();
    mSlots->setOnIdentify([this](const int player) { identifyPlayer(player); });
    mSlots->setOnClear([this](const int player) {
        footer()->setStatus("Player " + std::to_string(player) + " cleared — press Save.");
    });
    mSlots->setOnSave(
        [this](const std::map<int, std::string>& pins) { savePins(pins); });
    addChild(mSlots.get());
}

void MadPinEditorBase::requestDevices()
{
    pageRequest("devices.scan", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        if (!ok || mSlots == nullptr)
            return;
        mSlots->setDevices(devicesFromArray(MadJson::getMember(payload, "devices")));
    });
}

void MadPinEditorBase::applyPinsFromMerged(const rapidjson::Value& merged)
{
    if (mSlots != nullptr)
        mSlots->setPins(parsePinsForScope(merged, mScope));
}

void MadPinEditorBase::identifyPlayer(const int player)
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "identify",
        "Player " + std::to_string(player) + ": press a button on the pad you want…",
        [this, alive, player](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr || mSlots == nullptr)
                return;
            if (result->devicePinId.empty()) {
                footer()->flash("Couldn't identify — try a face button.", 4000, true);
                return;
            }
            mSlots->assignPin(player, result->devicePinId, result->deviceName);
            std::string badge;
            const std::string name {mSlots->describePin(result->devicePinId, badge)};
            footer()->setStatus("Player " + std::to_string(player) + " → " + name + "  [" +
                                badge + "] — press Save.");
        }));
}

void MadPinEditorBase::savePins(const std::map<int, std::string>& pins)
{
    const std::string scope {mScope};
    pageRequest(
        "policy.set_pins",
        [scope, pins](MadJson::Writer& writer) {
            writer.Key("scope");
            if (scope.empty())
                writer.Null();
            else
                writer.String(scope.c_str(), static_cast<rapidjson::SizeType>(scope.length()));
            writer.Key("pins");
            writer.StartObject();
            for (const auto& pin : pins) {
                const std::string key {std::to_string(pin.first)};
                writer.Key(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
                writer.String(pin.second.c_str(),
                              static_cast<rapidjson::SizeType>(pin.second.length()));
            }
            writer.EndObject();
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save the pins: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            // Rebuild from the on-disk truth the write returned.
            const rapidjson::Value& merged {MadJson::getMember(payload, "merged")};
            applyPinsFromMerged(merged);
            footer()->flash("Saved " + std::to_string(MadJson::getInt(payload, "saved")) +
                            " pin(s) [" + (mScope.empty() ? "global" : mScope) + "]");
            onSaved(merged);
        });
}

void MadPinEditorBase::onDevicesChanged(const rapidjson::Value& data)
{
    if (mSlots == nullptr)
        return;
    // The watch push carries a fresh scan; unsaved edits survive (setDevices
    // only re-describes). Fall back to an explicit scan if it ever doesn't.
    const rapidjson::Value& devices {MadJson::getMember(data, "devices")};
    if (devices.IsArray())
        mSlots->setDevices(devicesFromArray(devices));
    else
        requestDevices();
}

//  ── GuiMadPagePlayers (root, global scope) ──

GuiMadPagePlayers::GuiMadPagePlayers(GuiMadPanel* panel)
    : MadPinEditorBase {panel, "PLAYERS — PIN A PAD", ""}
    , mFocusTarget {FocusSlots}
    , mGridCookie {0}
    , mGridTop {0.0f}
    , mBuilt {false}
{
}

void GuiMadPagePlayers::build()
{
    setLoadingText("Loading pins…");
    mPanel->ensureDeviceWatch();

    pageRequest("policy.merged", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        if (!ok) {
            setLoadingText("");
            footer()->setStatus("Couldn't load the policy: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        buildLayout(MadJson::getMember(payload, "merged"));

        // Console art for the overrides grid (and the ● sublabels' truth lives
        // in the merged policy parsed above).
        pageRequest("systems.list", nullptr,
                    [this](bool ok, const rapidjson::Value& payload) {
                        if (ok) {
                            const rapidjson::Value& systems {
                                MadJson::getMember(payload, "systems")};
                            if (systems.IsArray()) {
                                for (rapidjson::SizeType i {0}; i < systems.Size(); ++i) {
                                    mSystemArt[MadJson::getString(systems[i], "name")] =
                                        MadJson::getString(systems[i], "art");
                                }
                            }
                        }
                        rebuildOverridesGrid(MadJson::nullValue());
                    },
                    10000);
        requestDevices();
    });
}

void GuiMadPagePlayers::buildLayout(const rapidjson::Value& merged)
{
    setLoadingText("");

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float miniHeight {Font::get(FONT_SIZE_MINI)->getHeight()};
    float y {mViewportPos.y};

    mIntro = std::make_shared<TextComponent>(
        "Pin a pad to a player so it stays that player across reconnects. Identify a slot, "
        "press a button on the pad, then Save.",
        Font::get(FONT_SIZE_SMALL), mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 0});
    mIntro->setPosition(mViewportPos.x, y);
    mIntro->setSize(mViewportSize.x, smallHeight);
    addChild(mIntro.get());
    y += smallHeight;

    mPinTypes = std::make_shared<TextComponent>(
        "Pin types —  ✓ MAC = port-agnostic (survives reconnects)  ·  ⚠ USB-port = re-pin if "
        "moved to another port  ·  ⚠ model-only = can't tell two of the same model apart.",
        Font::get(FONT_SIZE_MINI), mMenuColorSecondary, ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 0});
    mPinTypes->setPosition(mViewportPos.x, y);
    mPinTypes->setSize(mViewportSize.x, miniHeight);
    addChild(mPinTypes.get());
    y += miniHeight + smallHeight * 0.4f;

    mGlobalHeader = std::make_shared<TextComponent>("Global pins (apply to every game)",
                                                    Font::get(FONT_SIZE_SMALL), mMenuColorTitle,
                                                    ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mGlobalHeader->setPosition(mViewportPos.x, y);
    mGlobalHeader->setSize(mViewportSize.x, smallHeight);
    addChild(mGlobalHeader.get());
    y += smallHeight;

    createSlots();
    mSlots->setPosition(mViewportPos.x, y);
    mSlots->setSize(mViewportSize.x, mViewportSize.y * 0.40f);
    mSlots->onFocusGained();
    y += mSlots->getSize().y + smallHeight * 0.4f;

    mOverridesHeader = std::make_shared<TextComponent>(
        "Per-system overrides (win over global)", Font::get(FONT_SIZE_SMALL), mMenuColorTitle,
        ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mOverridesHeader->setPosition(mViewportPos.x, y);
    mOverridesHeader->setSize(mViewportSize.x, smallHeight);
    addChild(mOverridesHeader.get());
    y += smallHeight + smallHeight * 0.2f;

    mAddButton = std::make_shared<ButtonComponent>(
        "ADD PER-SYSTEM PINS", "add per-system pins",
        [this] { mPanel->pushPage(new GuiMadPagePlayersPicker(mPanel)); });
    mAddButton->setPosition(mViewportPos.x, y);
    addChild(mAddButton.get());
    y += mAddButton->getSize().y + smallHeight * 0.3f;

    mNoOverrides = std::make_shared<TextComponent>(
        "  (none — every system uses the global pins)", Font::get(FONT_SIZE_SMALL),
        mMenuColorSecondary, ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mNoOverrides->setPosition(mViewportPos.x, y);
    mNoOverrides->setSize(mViewportSize.x, smallHeight);
    addChild(mNoOverrides.get());

    mGridTop = y;
    mBuilt = true;

    applyPinsFromMerged(merged);
    rebuildOverridesGrid(merged);
    setFocusTarget(FocusSlots);
    footer()->setStatus("");
    mPanel->refreshHelpPrompts();
}

void GuiMadPagePlayers::rebuildOverridesGrid(const rapidjson::Value& merged)
{
    if (!mBuilt)
        return;

    // A null merged means "re-tile from the last parse" (art arrived late);
    // otherwise re-parse the override set from the given truth.
    if (merged.IsObject()) {
        mOverrideEntries.clear();
        const rapidjson::Value& systems {MadJson::getMember(merged, "systems")};
        if (systems.IsObject()) {
            for (auto it = systems.MemberBegin(); it != systems.MemberEnd(); ++it) {
                const rapidjson::Value& pins {MadJson::getMember(it->value, "pins")};
                if (pins.IsObject() && pins.MemberCount() > 0)
                    mOverrideEntries.emplace_back(it->name.GetString(), pinsSummary(pins));
            }
        }
        std::sort(mOverrideEntries.begin(), mOverrideEntries.end());
    }

    const int cursor {mGrid != nullptr ? mGrid->cursorIndex() : mGridCookie};
    if (mGrid != nullptr) {
        removeChild(mGrid.get());
        mGrid.reset();
    }

    if (mOverrideEntries.empty()) {
        mNoOverrides->setVisible(true);
        if (mFocusTarget == FocusGrid)
            setFocusTarget(FocusAdd);
        return;
    }
    mNoOverrides->setVisible(false);

    std::vector<MadTileGrid::Tile> tiles;
    for (const auto& entry : mOverrideEntries) {
        MadTileGrid::Tile tile;
        tile.key = entry.first;
        tile.label = entry.first;
        tile.sublabel = entry.second;
        const auto art = mSystemArt.find(entry.first);
        if (art != mSystemArt.end())
            tile.artPath = art->second;
        tiles.emplace_back(tile);
    }

    mGrid = std::make_shared<MadTileGrid>();
    mGrid->setPosition(mViewportPos.x, mGridTop);
    mGrid->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - mGridTop);
    mGrid->setTiles(tiles);
    mGrid->setOnPick([this](const std::string& system) {
        mPanel->pushPage(new GuiMadPagePlayersDetail(mPanel, system));
    });
    mGrid->setCursorIndex(cursor);
    addChild(mGrid.get());
}

void GuiMadPagePlayers::onSaved(const rapidjson::Value& merged)
{
    rebuildOverridesGrid(merged);
}

void GuiMadPagePlayers::onChildPopped()
{
    // A detail page may have added or emptied a per-system pin table; refresh
    // the overrides grid from fresh truth. Unsaved slot edits are kept.
    pageRequest("policy.merged", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        if (ok)
            rebuildOverridesGrid(MadJson::getMember(payload, "merged"));
    });
    // Devices may have come or gone while the picker/detail page was on top
    // (only the current page gets devices.watch pushes) — re-describe the
    // slots so the root doesn't show stale device descriptions.
    requestDevices();
}

void GuiMadPagePlayers::setFocusTarget(const int target)
{
    mFocusTarget = target;
    if (mSlots != nullptr) {
        if (target == FocusSlots)
            mSlots->onFocusGained();
        else
            mSlots->onFocusLost();
    }
    if (mAddButton != nullptr) {
        if (target == FocusAdd)
            mAddButton->onFocusGained();
        else
            mAddButton->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

bool GuiMadPagePlayers::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusSlots) {
        if (mSlots->input(config, input))
            return true;
        if (input.value != 0 && config->isMappedLike("down", input)) {
            setFocusTarget(FocusAdd);
            return true;
        }
        return false;
    }

    if (mFocusTarget == FocusAdd) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            mSlots->focusLastRow();
            setFocusTarget(FocusSlots);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            if (mGrid != nullptr)
                setFocusTarget(FocusGrid);
            return true;
        }
        if (config->isMappedTo("a", input))
            return mAddButton->input(config, input);
        return false;
    }

    // FocusGrid.
    if (mGrid == nullptr) {
        setFocusTarget(FocusAdd);
        return true;
    }
    if (input.value != 0 && config->isMappedLike("up", input)) {
        const int before {mGrid->cursorIndex()};
        mGrid->input(config, input);
        if (mGrid->cursorIndex() == before)
            setFocusTarget(FocusAdd); // Already on the top row.
        return true;
    }
    return mGrid->input(config, input);
}

void GuiMadPagePlayers::pageScroll(int direction)
{
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPagePlayers::getHelpPrompts()
{
    if (!mBuilt)
        return std::vector<HelpPrompt>();
    if (mFocusTarget == FocusSlots)
        return mSlots->getHelpPrompts();
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        return mGrid->getHelpPrompts();
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("up/down", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}

void GuiMadPagePlayers::onSaveFocus()
{
    mFocusCookie = mFocusTarget;
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void GuiMadPagePlayers::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocusTarget(mFocusCookie);
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}

//  ── GuiMadPagePlayersPicker ──

GuiMadPagePlayersPicker::GuiMadPagePlayersPicker(GuiMadPanel* panel)
    : MadPage {panel, "ADD PER-SYSTEM PINS"}
{
}

void GuiMadPagePlayersPicker::build()
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mIntro = std::make_shared<TextComponent>(
        "Pick a system to give its own pin overrides (it then appears under Per-system "
        "overrides).",
        Font::get(FONT_SIZE_SMALL), mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 0});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, smallHeight);
    addChild(mIntro.get());

    setLoadingText("Loading systems…");
    pageRequest(
        "systems.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't list systems: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            // Routable systems only: a hands-off system is never routed, so a
            // per-system pin table would be meaningless there.
            std::vector<MadTileGrid::Tile> tiles;
            const rapidjson::Value& systems {MadJson::getMember(payload, "systems")};
            if (systems.IsArray()) {
                for (rapidjson::SizeType i {0}; i < systems.Size(); ++i) {
                    const std::string sub {MadJson::getString(systems[i], "sub")};
                    if (sub == "hands-off")
                        continue;
                    MadTileGrid::Tile tile;
                    tile.key = MadJson::getString(systems[i], "name");
                    tile.label = tile.key;
                    tile.sublabel = sub;
                    tile.artPath = MadJson::getString(systems[i], "art");
                    tiles.emplace_back(tile);
                }
            }

            const float top {mIntro->getPosition().y + mIntro->getSize().y +
                             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
            mGrid = std::make_shared<MadTileGrid>();
            mGrid->setPosition(mViewportPos.x, top);
            mGrid->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - top);
            mGrid->setTiles(tiles);
            mGrid->setOnPick([this](const std::string& system) {
                mPanel->pushPage(new GuiMadPagePlayersDetail(mPanel, system));
            });
            mGrid->setCursorIndex(mFocusCookie);
            addChild(mGrid.get());
            mPanel->refreshHelpPrompts();
        },
        10000);
}

bool GuiMadPagePlayersPicker::input(InputConfig* config, Input input)
{
    if (mGrid != nullptr)
        return mGrid->input(config, input);
    return false;
}

void GuiMadPagePlayersPicker::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPagePlayersPicker::getHelpPrompts()
{
    if (mGrid != nullptr)
        return mGrid->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPagePlayersPicker::onSaveFocus()
{
    if (mGrid != nullptr)
        mFocusCookie = mGrid->cursorIndex();
}

void GuiMadPagePlayersPicker::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mFocusCookie);
}

//  ── GuiMadPagePlayersDetail ──

GuiMadPagePlayersDetail::GuiMadPagePlayersDetail(GuiMadPanel* panel, const std::string& system)
    : MadPinEditorBase {panel, "PINS: " + Utils::String::toUpper(system), system}
{
}

void GuiMadPagePlayersDetail::build()
{
    mPanel->ensureDeviceWatch();

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mIntro = std::make_shared<TextComponent>(
        "Per-system pins for " + mScope + " — these OVERRIDE the global pins for this system "
        "only. Clear them all to fall back to the global pins.",
        Font::get(FONT_SIZE_SMALL), mMenuColorSecondary, ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 0});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, smallHeight);
    addChild(mIntro.get());

    const float top {mViewportPos.y + smallHeight + smallHeight * 0.5f};
    createSlots();
    mSlots->setPosition(mViewportPos.x, top);
    mSlots->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - top);
    mSlots->onFocusGained();

    pageRequest("policy.merged", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        if (!ok) {
            footer()->setStatus("Couldn't load the policy: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        applyPinsFromMerged(MadJson::getMember(payload, "merged"));
    });
    requestDevices();
    mPanel->refreshHelpPrompts();
}

bool GuiMadPagePlayersDetail::input(InputConfig* config, Input input)
{
    if (mSlots != nullptr && mSlots->input(config, input))
        return true;
    // Edge moves have nowhere to go on this page; don't let them leak.
    if (input.value != 0 &&
        (config->isMappedLike("up", input) || config->isMappedLike("down", input)))
        return true;
    return false;
}

std::vector<HelpPrompt> GuiMadPagePlayersDetail::getHelpPrompts()
{
    if (mSlots != nullptr)
        return mSlots->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPagePlayersDetail::onSaveFocus()
{
    if (mSlots != nullptr)
        mFocusCookie = mSlots->focusCookie();
}

void GuiMadPagePlayersDetail::onRestoreFocus()
{
    if (mSlots != nullptr)
        mSlots->setFocusCookie(mFocusCookie);
}
