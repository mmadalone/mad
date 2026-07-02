//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePlayers.cpp
//
//  MAD control panel: Players section (deck-patches).
//

#include "guis/mad/pages/GuiMadPagePlayers.h"

#include "Sound.h"
#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "utils/StringUtil.h"

#include <algorithm>
#include <cstdlib>
#include "guis/mad/MadTheme.h"

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
            // "label" is the friendly, port-aware name (e.g. "X-Arcade P1"/"P2" split by
            // USB interface); the backend always populates it (falls back to the raw name).
            result.emplace_back(MadPlayerSlots::Device {MadJson::getString(device, "label"),
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

void MadPinEditorBase::createSlots(GuiComponent* parent)
{
    mSlots = std::make_shared<MadPlayerSlots>();
    mSlots->setOnIdentify([this](const int player) { identifyPlayer(player); });
    mSlots->setOnClear([this](const int player) {
        footer()->setStatus("Player " + std::to_string(player) + " cleared — press Save.");
    });
    mSlots->setOnSave(
        [this](const std::map<int, std::string>& pins) { savePins(pins); });
    (parent != nullptr ? parent : this)->addChild(mSlots.get());
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
            // Clear any "Player N → … press Save." sticky — it's resolved now.
            footer()->setStatus("");
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
    , mScrollCookie {0.0f}
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

    // The whole content column scrolls as one (Tk _scroll parity): every
    // child lives inside mScroll at VIEW-LOCAL coordinates. The slots hold
    // all 8 players at full height (no more 40%-of-viewport squeeze) and the
    // overrides grid gets its full height below them.
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float miniHeight {Font::get(FONT_SIZE_MINI)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mIntro = std::make_shared<TextComponent>(
        "Without a pin, a controller can be given a different player number each time it "
        "reconnects or the console restarts. Pinning locks one physical pad to a fixed "
        "player, everywhere. Identify a slot, press a button on the pad, then Save.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f); // Autosize: wrap, never ellipsize.
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.2f;

    mPinTypes = std::make_shared<TextComponent>(
        "Pin types:\n"
        "•  ✓ MAC — port-agnostic (survives reconnects)\n"
        "•  ⚠ USB-port — re-pin if moved to another port\n"
        "•  ⚠ model-only — can't tell two of the same model apart",
        Font::get(FONT_SIZE_MINI), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mPinTypes->setPosition(0.0f, y);
    mPinTypes->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mPinTypes.get());
    y += mPinTypes->getSize().y + smallHeight * 0.4f;
    (void)miniHeight;

    mGlobalHeader = std::make_shared<TextComponent>("Global pins (apply to every game)",
                                                    Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title),
                                                    ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mGlobalHeader->setPosition(0.0f, y);
    mGlobalHeader->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mGlobalHeader.get());
    y += smallHeight;

    createSlots(mScroll.get());
    mSlots->setPosition(0.0f, y);
    // Two-pass sizing: the first pass computes the row metrics, the second
    // gives the editor its full height (internal scroll becomes a no-op).
    mSlots->setSize(mViewportSize.x, 1.0f);
    mSlots->setSize(mViewportSize.x, std::max(1.0f, mSlots->contentHeight()));
    mSlots->onFocusGained();
    y += mSlots->getSize().y + smallHeight * 0.4f;

    mOverridesHeader = std::make_shared<TextComponent>(
        "Per-system overrides (win over global)", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title),
        ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mOverridesHeader->setPosition(0.0f, y);
    mOverridesHeader->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mOverridesHeader.get());
    y += smallHeight + smallHeight * 0.2f;

    mAddButton = std::make_shared<ButtonComponent>(
        "ADD PER-SYSTEM PINS", "add per-system pins",
        [this] { mPanel->pushPage(new GuiMadPagePlayersPicker(mPanel)); });
    mAddButton->setPosition(0.0f, y);
    mScroll->addChild(mAddButton.get());
    y += mAddButton->getSize().y + smallHeight * 0.3f;

    mNoOverrides = std::make_shared<TextComponent>(
        "  (none — every system uses the global pins)", Font::get(FONT_SIZE_SMALL),
        MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mNoOverrides->setPosition(0.0f, y);
    mNoOverrides->setSize(mViewportSize.x, smallHeight);
    mScroll->addChild(mNoOverrides.get());

    mGridTop = y;
    mBuilt = true;

    applyPinsFromMerged(merged);
    rebuildOverridesGrid(merged);
    setFocusTarget(FocusSlots);
    followFocus();
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

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const int cursor {mGrid != nullptr ? mGrid->cursorIndex() : mGridCookie};
    if (mGrid != nullptr) {
        mScroll->removeChild(mGrid.get()); // The view is the parent, not the page.
        mGrid.reset();
    }

    if (mOverrideEntries.empty()) {
        mNoOverrides->setVisible(true);
        // setContentHeight clamps the offset — clearing the last override
        // while scrolled to the bottom must not strand the view down there.
        mScroll->setContentHeight(mGridTop + mNoOverrides->getSize().y + smallHeight * 0.5f);
        if (mFocusTarget == FocusGrid) {
            setFocusTarget(FocusAdd);
            followFocus();
        }
        mPanel->refreshHelpPrompts(); // The ltrt prompt may have come or gone.
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
    mGrid->setPosition(0.0f, mGridTop);
    // Two-pass sizing: columns need the real width, the full height needs the
    // tiles laid out. At full height the grid's internal scroll is a clamped
    // no-op — the page scrolls it through mScroll instead.
    mGrid->setSize(mViewportSize.x, 1.0f);
    mGrid->setTiles(tiles);
    mGrid->setSize(mViewportSize.x, std::max(1.0f, mGrid->contentHeight()));
    mGrid->setOnPick([this](const std::string& system) {
        mPanel->pushPage(new GuiMadPagePlayersDetail(mPanel, system));
    });
    mGrid->setCursorIndex(cursor);
    if (mFocusTarget == FocusGrid)
        mGrid->onFocusGained(); // The fresh grid inherits the page focus.
    mScroll->addChild(mGrid.get());
    mScroll->setContentHeight(mGridTop + mGrid->getSize().y + smallHeight * 0.5f);
    if (mFocusTarget == FocusGrid)
        followFocus(); // Re-snap after an async refresh moved/resized the grid.
    mPanel->refreshHelpPrompts();
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
    if (mGrid != nullptr) {
        if (target == FocusGrid)
            mGrid->onFocusGained();
        else
            mGrid->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPagePlayers::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPagePlayers::followFocus()
{
    if (mScroll == nullptr)
        return;
    float top {0.0f};
    float bottom {0.0f};
    switch (mFocusTarget) {
        case FocusSlots: {
            const glm::vec2 row {mSlots->focusRowRect()};
            // SAVE (the topmost focusable): reveal the intro/header context.
            top = mSlots->focusCookie() == 0 ? 0.0f : mSlots->getPosition().y + row.x;
            bottom = mSlots->getPosition().y + row.y;
            break;
        }
        case FocusAdd: {
            top = mAddButton->getPosition().y;
            bottom = top + mAddButton->getSize().y;
            break;
        }
        case FocusGrid: {
            if (mGrid == nullptr)
                return;
            const glm::vec2 row {mGrid->cursorRowRect()};
            top = mGrid->getPosition().y + row.x;
            bottom = mGrid->getPosition().y + row.y;
            break;
        }
        default:
            return;
    }
    mScroll->ensureVisible(top, bottom);
}

std::vector<MadPage::PagedTarget> GuiMadPagePlayers::pagedTargets() const
{
    // Layout order == top order (pickPagedTarget relies on it).
    std::vector<PagedTarget> targets;
    for (int row {0}; row <= MadPlayerSlots::PLAYER_COUNT; ++row) {
        const glm::vec2 rect {mSlots->rowRect(row)};
        targets.push_back({FocusSlots, row, mSlots->getPosition().y + rect.x,
                           mSlots->getPosition().y + rect.y});
    }
    targets.push_back({FocusAdd, -1, mAddButton->getPosition().y,
                       mAddButton->getPosition().y + mAddButton->getSize().y});
    if (mGrid != nullptr) {
        for (int row {0}; row < mGrid->rows(); ++row) {
            const glm::vec2 rect {mGrid->rowRect(row)};
            targets.push_back({FocusGrid, row, mGrid->getPosition().y + rect.x,
                               mGrid->getPosition().y + rect.y});
        }
    }
    return targets;
}

void GuiMadPagePlayers::applyPagedTarget(const PagedTarget& target)
{
    if (target.id == FocusSlots) {
        mSlots->setFocusCookie(target.aux); // Silent row move (0 = SAVE).
    }
    else if (target.id == FocusGrid && mGrid != nullptr) {
        // Land on the picked row, keeping the cursor's column (silent move).
        const int columns {std::max(1, mGrid->columns())};
        const int column {mGrid->cursorIndex() % columns};
        mGrid->setCursorIndex(
            std::min(target.aux * columns + column, mGrid->tileCount() - 1));
    }
    setFocusTarget(target.id);
}

bool GuiMadPagePlayers::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusSlots) {
        if (mSlots->input(config, input)) {
            followFocus(); // The focused row may have changed.
            return true;
        }
        if (input.value != 0 && config->isMappedLike("down", input)) {
            moveFocus(FocusAdd);
            return true;
        }
        return false;
    }

    if (mFocusTarget == FocusAdd) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            mSlots->focusLastRow();
            moveFocus(FocusSlots);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            if (mGrid != nullptr)
                moveFocus(FocusGrid);
            return true;
        }
        if (config->isMappedTo("a", input))
            return mAddButton->input(config, input);
        return false;
    }

    // FocusGrid.
    if (mGrid == nullptr) {
        moveFocus(FocusAdd);
        return true;
    }
    if (input.value != 0 && config->isMappedLike("up", input)) {
        const int before {mGrid->cursorIndex()};
        mGrid->input(config, input);
        if (mGrid->cursorIndex() == before)
            moveFocus(FocusAdd); // Already on the top row.
        else
            followFocus();
        return true;
    }
    if (mGrid->input(config, input)) {
        followFocus(); // The cursor row may have changed.
        return true;
    }
    return false;
}

void GuiMadPagePlayers::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr)
        return;
    // Tk _scroll parity — page the VIEW, then land focus on the lowest (RT) /
    // highest (LT) control whose top edge is inside the new window; see
    // GuiMadPageQuitCombo::pageScroll for the full notes.
    const std::vector<PagedTarget> targets {pagedTargets()};
    bool moved {false};
    if (mScroll->overflows())
        moved = mScroll->pageScroll(direction);
    const float viewTop {mScroll->overflows() ? mScroll->scrollOffset() : 0.0f};
    const float viewBottom {viewTop + (mScroll->overflows() ? mScroll->getSize().y :
                                                              mScroll->contentHeight())};
    const int pick {pickPagedTarget(targets, direction, viewTop, viewBottom)};
    if (pick >= 0) {
        const PagedTarget& target {targets[pick]};
        bool changed {target.id != mFocusTarget};
        if (!changed && target.id == FocusSlots)
            changed = target.aux != mSlots->focusCookie();
        if (!changed && target.id == FocusGrid && mGrid != nullptr)
            changed = target.aux != mGrid->cursorIndex() / std::max(1, mGrid->columns());
        applyPagedTarget(target);
        followFocus();
        if (changed)
            moved = true;
    }
    // Silent when nothing happened (repeated RT at the bottom must not click).
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPagePlayers::getHelpPrompts()
{
    if (!mBuilt)
        return std::vector<HelpPrompt>();
    std::vector<HelpPrompt> prompts;
    if (mFocusTarget == FocusSlots)
        prompts = mSlots->getHelpPrompts();
    else if (mFocusTarget == FocusGrid && mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    else {
        prompts.push_back(HelpPrompt("up/down", "choose"));
        prompts.push_back(HelpPrompt("a", "select"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPagePlayers::onSaveFocus()
{
    mFocusCookie = mFocusTarget;
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPagePlayers::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocusTarget(mFocusCookie);
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}

//  ── GuiMadPagePlayersPicker ──

GuiMadPagePlayersPicker::GuiMadPagePlayersPicker(GuiMadPanel* panel)
    : MadPage {panel, "ADD PER-SYSTEM PINS"}
{
}

void GuiMadPagePlayersPicker::build()
{
    mIntro = std::make_shared<TextComponent>(
        "Pick a system to give its own pin overrides (it then appears under Per-system "
        "overrides).",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, 0.0f);
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
            mGrid->onFocusGained(); // Only focusable here.
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
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
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
