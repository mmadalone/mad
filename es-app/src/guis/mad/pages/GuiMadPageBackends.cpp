//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageBackends.cpp
//
//  MAD control panel: Backends section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageBackends.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "utils/StringUtil.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

//  ── GuiMadPageBackends (root) ──

GuiMadPageBackends::GuiMadPageBackends(GuiMadPanel* panel)
    : MadPage {panel, "BACKENDS (CONTROLLERS)"}
    , mGridCookie {0}
    , mScrollCookie {0.0f}
{
}

void GuiMadPageBackends::build()
{
    setLoadingText("Loading backends…");
    pageRequest("backends.list", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        setLoadingText("");
        if (!ok) {
            footer()->setStatus("Couldn't list backends: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        rebuild(payload);
    });
}

void GuiMadPageBackends::onChildPopped()
{
    // A detail page may have changed pad_classes / handheld_class — the ⚠
    // no-players state and the key summary need fresh truth.
    build();
}

void GuiMadPageBackends::rebuild(const rapidjson::Value& result)
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    mIntro.reset();
    mGrid.reset();
    mHiddenNote.reset();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float miniHeight {Font::get(FONT_SIZE_MINI)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mIntro = std::make_shared<TextComponent>(
        "Per-emulator controller settings. Pick a backend to edit which pads are players, "
        "how many slots, profiles, and config location.",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.4f;

    std::vector<MadTileGrid::Tile> tiles;
    const rapidjson::Value& backends {MadJson::getMember(result, "backends")};
    if (backends.IsArray()) {
        for (rapidjson::SizeType i {0}; i < backends.Size(); ++i) {
            const rapidjson::Value& row {backends[i]};
            MadTileGrid::Tile tile;
            tile.key = MadJson::getString(row, "name");
            tile.label = tile.key;
            tile.warn = MadJson::getBool(row, "no_players");
            tile.sublabel =
                tile.warn ? "⚠ no players" : MadJson::getString(row, "summary");
            const rapidjson::Value& art {MadJson::getMember(row, "art")};
            if (art.IsArray() && art.Size() > 0 && art[0].IsString())
                tile.artPath = art[0].GetString();
            tiles.emplace_back(tile);
        }
    }

    if (tiles.empty()) {
        setLoadingText("No backends configured in controller-policy.toml.");
    }
    else {
        mGrid = std::make_shared<MadTileGrid>();
        mGrid->setPosition(0.0f, y);
        mGrid->setSize(mViewportSize.x, 1.0f);
        mGrid->setTiles(tiles);
        mGrid->setSize(mViewportSize.x, std::max(1.0f, mGrid->contentHeight()));
        mGrid->setOnPick([this](const std::string& backend) {
            mPanel->pushPage(new GuiMadPageBackendDetail(mPanel, backend));
        });
        mGrid->setCursorIndex(mGridCookie);
        mGrid->onFocusGained(); // The grid is this page's only focusable.
        mScroll->addChild(mGrid.get());
        y += mGrid->getSize().y;
    }

    std::string hidden;
    const rapidjson::Value& hiddenArr {MadJson::getMember(result, "hidden")};
    if (hiddenArr.IsArray()) {
        for (rapidjson::SizeType i {0}; i < hiddenArr.Size(); ++i) {
            if (!hiddenArr[i].IsString())
                continue;
            if (!hidden.empty())
                hidden.append(", ");
            hidden.append(hiddenArr[i].GetString());
        }
    }
    if (!hidden.empty()) {
        mHiddenNote = std::make_shared<TextComponent>(
            "Hidden (no games in ES-DE): " + hidden, Font::get(FONT_SIZE_MINI),
            MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 1});
        mHiddenNote->setPosition(0.0f, y + smallHeight * 0.4f);
        mHiddenNote->setSize(mViewportSize.x, 0.0f);
        mScroll->addChild(mHiddenNote.get());
        y += smallHeight * 0.4f + mHiddenNote->getSize().y;
    }
    (void)miniHeight;

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);
    followFocus();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBackends::followFocus()
{
    if (mScroll == nullptr || mGrid == nullptr)
        return;
    const glm::vec2 row {mGrid->cursorRowRect()};
    // Top row: reveal the intro above it too.
    if (mGrid->cursorIndex() / std::max(1, mGrid->columns()) == 0)
        mScroll->ensureVisible(0.0f, mGrid->getPosition().y + row.y);
    else
        mScroll->ensureVisible(mGrid->getPosition().y + row.x, mGrid->getPosition().y + row.y);
}

bool GuiMadPageBackends::input(InputConfig* config, Input input)
{
    if (mGrid == nullptr)
        return false;
    if (mGrid->input(config, input)) {
        followFocus();
        return true;
    }
    return false;
}

void GuiMadPageBackends::pageScroll(int direction)
{
    if (mScroll == nullptr || mGrid == nullptr)
        return;
    if (!mScroll->overflows()) {
        mGrid->pageScroll(direction);
        followFocus();
        return;
    }
    std::vector<PagedTarget> targets;
    for (int row {0}; row < mGrid->rows(); ++row) {
        const glm::vec2 rect {mGrid->rowRect(row)};
        targets.push_back({0, row, mGrid->getPosition().y + rect.x,
                           mGrid->getPosition().y + rect.y});
    }
    bool moved {mScroll->pageScroll(direction)};
    const float viewTop {mScroll->scrollOffset()};
    const int pick {
        pickPagedTarget(targets, direction, viewTop, viewTop + mScroll->getSize().y)};
    if (pick >= 0) {
        const int columns {std::max(1, mGrid->columns())};
        const int column {mGrid->cursorIndex() % columns};
        const int target {
            std::min(targets[pick].aux * columns + column, mGrid->tileCount() - 1)};
        if (target != mGrid->cursorIndex()) {
            mGrid->setCursorIndex(target);
            moved = true;
        }
        followFocus();
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPageBackends::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mGrid != nullptr)
        prompts = mGrid->getHelpPrompts();
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageBackends::onSaveFocus()
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageBackends::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}

//  ── GuiMadPageBackendChoice ──

GuiMadPageBackendChoice::GuiMadPageBackendChoice(
    GuiMadPanel* panel,
    const std::string& title,
    const std::string& caption,
    const std::vector<std::pair<std::string, std::string>>& options,
    const std::string& current,
    const std::function<void(const std::string&)>& onChoose)
    : MadPage {panel, Utils::String::toUpper(title)}
    , mCaption {caption}
    , mOptions {options}
    , mCurrent {current}
    , mOnChoose {onChoose}
{
}

void GuiMadPageBackendChoice::build()
{
    float y {mViewportPos.y};
    if (!mCaption.empty()) {
        mCaptionText = std::make_shared<TextComponent>(
            mCaption, Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
            ALIGN_CENTER, glm::ivec2 {0, 1});
        mCaptionText->setPosition(mViewportPos.x, y);
        mCaptionText->setSize(mViewportSize.x, 0.0f);
        addChild(mCaptionText.get());
        y += mCaptionText->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f;
    }

    mList = std::make_shared<ComponentList>();
    mList->setPosition(mViewportPos.x, y);
    mList->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - y);
    addChild(mList.get());

    for (const auto& option : mOptions) {
        const std::string value {option.first};
        ComponentListRow row;
        row.addElement(std::make_shared<TextComponent>(
                           option.second, Font::get(FONT_SIZE_MEDIUM), MadTheme::color(MadColor::Primary),
                           ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0}),
                       true);
        row.makeAcceptInputHandler([this, value] {
            NavigationSounds::getInstance().playThemeNavigationSound(SELECTSOUND);
            if (mOnChoose)
                mOnChoose(value);
            mPanel->popPage(); // 'this' dies here; nothing below touches members.
        });
        mList->addRow(row, value == mCurrent);
    }

    mList->onFocusGained();
    mPanel->refreshHelpPrompts();
}

bool GuiMadPageBackendChoice::input(InputConfig* config, Input input)
{
    if (mList != nullptr)
        return mList->input(config, input);
    return false;
}

void GuiMadPageBackendChoice::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->moveCursor(direction * 6);
}

std::vector<HelpPrompt> GuiMadPageBackendChoice::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("up/down", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}

//  ── GuiMadPageBackendDetail ──

GuiMadPageBackendDetail::GuiMadPageBackendDetail(GuiMadPanel* panel, const std::string& backend)
    : MadPage {panel, "BACKEND: " + Utils::String::toUpper(backend)}
    , mBackend {backend}
    , mFocus {0}
    , mFocusCookie {0}
    , mNextRow {0}
    , mScrollCookie {0.0f}
    , mBuilt {false}
    , mSuppressChildPopRefresh {false}
{
}

void GuiMadPageBackendDetail::build()
{
    setLoadingText("Loading " + mBackend + "…");
    refresh();
}

void GuiMadPageBackendDetail::refresh()
{
    const std::string backend {mBackend};
    pageRequest(
        "backends.describe",
        [backend](MadJson::Writer& writer) {
            writer.Key("backend");
            writer.String(backend.c_str(), static_cast<rapidjson::SizeType>(backend.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load " + mBackend + ": " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        });
}

void GuiMadPageBackendDetail::onChildPopped()
{
    if (mSuppressChildPopRefresh) {
        // A profiles.apply_slot is in flight; its response refreshes once.
        mSuppressChildPopRefresh = false;
        return;
    }
    refresh();
}

void GuiMadPageBackendDetail::clearLayout()
{
    // Children first (dtors self-detach from the live scroll view), THEN the
    // scroll view — removeChild on the wrong parent would dangle.
    mControls.clear();
    mWidgets.clear();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }
}

void GuiMadPageBackendDetail::setBackendKey(const std::string& key,
                                            const MadJson::ParamsWriter& valueWriter,
                                            const std::string& shown)
{
    const std::string backend {mBackend};
    pageRequest(
        "policy.set_backend_key",
        [backend, key, valueWriter](MadJson::Writer& writer) {
            writer.Key("backend");
            writer.String(backend.c_str(), static_cast<rapidjson::SizeType>(backend.length()));
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("value");
            valueWriter(writer);
        },
        [this, key, shown](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save " + mBackend + "." + key + ": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                refresh(); // Resync the optimistic control to on-disk truth.
                return;
            }
            footer()->setStatus("");
            // Let the backend name the outcome. "Saved <backend>.<key> = <value>"
            // is right for a SETTING, which is what almost every key here is. But
            // a few keys are ACTIONS riding the same RPC (openbor's "Reset a
            // game's controls", routed by a magic key), and for those the default
            // is both ugly -- it leaks the raw key, e.g.
            // "Saved openbor.__openbor_reseed__ = Golden Axe" -- and untrue:
            // nothing was saved. Those return a "flash"; everything else is
            // unchanged.
            footer()->flash(MadJson::getString(payload, "flash",
                                               "Saved " + mBackend + "." + key +
                                                   " = " + shown));
        });
}

void GuiMadPageBackendDetail::openChoice(
    const std::string& title,
    const std::string& caption,
    const std::vector<std::pair<std::string, std::string>>& options,
    const std::string& current,
    const std::function<void(const std::string&)>& onChoose)
{
    mPanel->pushPage(
        new GuiMadPageBackendChoice(mPanel, title, caption, options, current, onChoose));
}

void GuiMadPageBackendDetail::rebuild(const rapidjson::Value& result)
{
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    if (mBuilt)
        mFocusCookie = mFocus;
    clearLayout();

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float W {mViewportSize.x};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};
    mNextRow = 0;

    auto addText = [this, W, &y](const std::string& text, const float fontSize,
                                 const unsigned int color, const float padAfter) {
        auto component = std::make_shared<TextComponent>(text, Font::get(fontSize), color,
                                                         ALIGN_LEFT, ALIGN_CENTER,
                                                         glm::ivec2 {0, 1});
        component->setPosition(0.0f, y);
        component->setSize(W, 0.0f); // Autosize: wrapped height.
        mScroll->addChild(component.get());
        mWidgets.emplace_back(component);
        y += component->getSize().y + padAfter;
    };
    auto header = [&addText, smallHeight](const std::string& label) {
        addText(label, FONT_SIZE_SMALL, MadTheme::color(MadColor::Title), smallHeight * 0.15f);
    };
    auto caption = [&addText, smallHeight](const std::string& help) {
        if (!help.empty())
            addText("    " + help, FONT_SIZE_MINI, MadTheme::color(MadColor::Secondary), smallHeight * 0.45f);
    };

    if (MadJson::getBool(result, "warn_empty")) {
        addText("⚠  No player pad families selected — this backend's SDL whitelist is empty, "
                "so games launched on it will receive NO controllers. Select at least one "
                "Player pad family below (or set a handheld pad).",
                FONT_SIZE_SMALL, MadTheme::color(MadColor::Red), smallHeight * 0.5f);
    }

    const rapidjson::Value& knobs {MadJson::getMember(result, "knobs")};
    if (knobs.IsArray()) {
        for (rapidjson::SizeType i {0}; i < knobs.Size(); ++i) {
            const rapidjson::Value& knob {knobs[i]};
            const std::string key {MadJson::getString(knob, "key")};
            const std::string kind {MadJson::getString(knob, "kind")};
            const std::string label {MadJson::getString(knob, "label", key)};
            const std::string help {MadJson::getString(knob, "help")};

            if (kind == "bool") {
                // A single toggle's label IS the switch's name, so render it INLINE on the chip
                // (one [switch] label row) — NOT as a redundant green section header above a bare
                // switch (matches how GuiMadPageEmuSettings renders bools). An explicit
                // "toggle_label" still overrides the inline text; empty/absent falls back to label.
                auto chips = std::make_shared<MadChipRow>();
                chips->setPosition(0.0f, y);
                chips->setSize(W, 1.0f);
                std::string inlineLabel {MadJson::getString(knob, "toggle_label", "")};
                if (inlineLabel.empty())
                    inlineLabel = label;
                chips->setChips({{key, inlineLabel, MadJson::getBool(knob, "value")}});
                chips->setSize(W, std::max(1.0f, chips->contentHeight()));
                chips->setOnToggle([this, key](const std::string&, const bool on) {
                    setBackendKey(
                        key, [on](MadJson::Writer& writer) { writer.Bool(on); },
                        on ? "true" : "false");
                });
                mScroll->addChild(chips.get());
                mWidgets.emplace_back(chips);
                mControls.push_back({Control::Type::Chips, chips.get(), y,
                                     y + chips->getSize().y, mNextRow++});
                y += chips->getSize().y + smallHeight * 0.15f;
                caption(help);
            }
            else if (kind == "class_set" || kind == "slot_set") {
                header(label);
                std::vector<MadChipRow::Chip> chipDefs;
                const bool isSlots {kind == "slot_set"};
                const rapidjson::Value& items {
                    MadJson::getMember(knob, isSlots ? "slots" : "candidates")};
                if (items.IsArray()) {
                    for (rapidjson::SizeType j {0}; j < items.Size(); ++j) {
                        const rapidjson::Value& item {items[j]};
                        const std::string value {
                            isSlots ? std::to_string(MadJson::getInt(item, "slot")) :
                                      MadJson::getString(item, "value")};
                        chipDefs.push_back({value,
                                            MadJson::getString(item, "label", value),
                                            MadJson::getBool(item, "on")});
                    }
                }
                auto chips = std::make_shared<MadChipRow>();
                chips->setPosition(0.0f, y);
                chips->setSize(W, 1.0f);
                chips->setChips(chipDefs);
                chips->setSize(W, std::max(1.0f, chips->contentHeight()));
                // weak_ptr: the rollback below may fire after a rebuild() has
                // destroyed this chip row (in-flight write vs an interleaved
                // re-describe) — a raw pointer would dangle. A shared_ptr
                // would self-cycle (the callback lives inside the widget).
                std::weak_ptr<MadChipRow> weakChips {chips};
                const std::string backend {mBackend};
                chips->setOnToggle([this, backend, key, isSlots, weakChips](
                                       const std::string& value, const bool on) {
                    pageRequest(
                        "policy.set_backend_list_member",
                        [backend, key, isSlots, value, on](MadJson::Writer& writer) {
                            writer.Key("backend");
                            writer.String(backend.c_str(),
                                          static_cast<rapidjson::SizeType>(backend.length()));
                            writer.Key("key");
                            writer.String(key.c_str(),
                                          static_cast<rapidjson::SizeType>(key.length()));
                            writer.Key("member");
                            if (isSlots)
                                writer.Int(std::stoi(value));
                            else
                                writer.String(value.c_str(),
                                              static_cast<rapidjson::SizeType>(value.length()));
                            writer.Key("present");
                            writer.Bool(on);
                            if (isSlots) {
                                writer.Key("is_int");
                                writer.Bool(true);
                            }
                        },
                        [this, key, value, on, weakChips](bool ok,
                                                          const rapidjson::Value& payload) {
                            if (!ok) {
                                if (auto chipRow = weakChips.lock())
                                    chipRow->setChipState(value, !on); // Roll back.
                                footer()->flash(
                                    "Couldn't save " + mBackend + "." + key + ": " +
                                        MadJson::getString(payload, "message",
                                                           "unknown error"),
                                    4000, true);
                                return;
                            }
                            footer()->setStatus("");
                            footer()->flash("Saved " + mBackend + "." + key);
                        });
                });
                mScroll->addChild(chips.get());
                mWidgets.emplace_back(chips);
                mControls.push_back({Control::Type::Chips, chips.get(), y,
                                     y + chips->getSize().y, mNextRow++});
                y += chips->getSize().y + smallHeight * 0.15f;
                caption(help);
            }
            else if (kind == "int") {
                header(label);
                const int lo {MadJson::getInt(knob, "lo", 1)};
                const int hi {MadJson::getInt(knob, "hi", 4)};
                auto stepper = std::make_shared<MadStepper>(
                    label, static_cast<float>(lo), static_cast<float>(hi), 1.0f,
                    [](const float value) {
                        return std::to_string(static_cast<int>(std::lround(value)));
                    },
                    [this, key](const float value) {
                        const int intValue {static_cast<int>(std::lround(value))};
                        setBackendKey(
                            key,
                            [intValue](MadJson::Writer& writer) { writer.Int(intValue); },
                            std::to_string(intValue));
                    });
                stepper->setPosition(0.0f, y);
                stepper->setSize(W * 0.45f, Font::get(FONT_SIZE_MEDIUM)->getHeight() * 1.4f);
                stepper->setValue(static_cast<float>(MadJson::getInt(knob, "value", lo)));
                mScroll->addChild(stepper.get());
                mWidgets.emplace_back(stepper);
                mControls.push_back({Control::Type::Stepper, stepper.get(), y,
                                     y + stepper->getSize().y, mNextRow++});
                y += stepper->getSize().y + smallHeight * 0.15f;
                caption(help);
            }
            else if (kind == "choice") {
                header(label);
                const std::string current {MadJson::getString(knob, "value")};
                const std::string currentLabel {
                    MadJson::getString(knob, "value_label", "none")};
                std::vector<std::pair<std::string, std::string>> options;
                const rapidjson::Value& optionArr {MadJson::getMember(knob, "options")};
                if (optionArr.IsArray()) {
                    for (rapidjson::SizeType j {0}; j < optionArr.Size(); ++j)
                        options.emplace_back(MadJson::getString(optionArr[j], "value"),
                                             MadJson::getString(optionArr[j], "label"));
                }
                auto button = std::make_shared<ButtonComponent>(
                    label + ":  " + currentLabel, label, [this, label, help, options, current,
                                                          key] {
                        std::weak_ptr<int> alive {pageAlive()};
                        openChoice(label, help, options, current,
                                   [this, alive, key](const std::string& value) {
                                       if (alive.expired())
                                           return;
                                       setBackendKey(
                                           key,
                                           [value](MadJson::Writer& writer) {
                                               writer.String(
                                                   value.c_str(),
                                                   static_cast<rapidjson::SizeType>(
                                                       value.length()));
                                           },
                                           value.empty() ? "none" : value);
                                   });
                    });
                button->setPosition(0.0f, y);
                mScroll->addChild(button.get());
                mWidgets.emplace_back(button);
                mControls.push_back({Control::Type::Button, button.get(), y,
                                     y + button->getSize().y, mNextRow++});
                y += button->getSize().y + smallHeight * 0.15f;
                caption(help);
            }
            else if (kind == "slot_profiles") {
                header(label);
                caption(help);
                std::vector<std::string> profiles;
                const rapidjson::Value& profileArr {MadJson::getMember(knob, "profiles")};
                if (profileArr.IsArray()) {
                    for (rapidjson::SizeType j {0}; j < profileArr.Size(); ++j) {
                        if (profileArr[j].IsString())
                            profiles.emplace_back(profileArr[j].GetString());
                    }
                }
                if (profiles.empty()) {
                    addText("  (no profiles found in " +
                                MadJson::getString(knob, "profiles_dir") + ")",
                            FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), smallHeight * 0.3f);
                    continue;
                }
                const std::string slotLabel {
                    MadJson::getString(knob, "slot_label", "Slot")};
                std::vector<std::pair<std::string, std::string>> options;
                options.emplace_back("", "(clear)");
                for (const std::string& profile : profiles)
                    options.emplace_back(profile, profile);
                const rapidjson::Value& slots {MadJson::getMember(knob, "slots")};
                // The 8 slot buttons flow side by side (one focus row,
                // wrapping) instead of burning 8 stacked lines.
                const int slotRowId {mNextRow++};
                const float slotGap {smallHeight * 0.5f};
                float slotX {0.0f};
                float slotLineHeight {0.0f};
                if (slots.IsArray()) {
                    for (rapidjson::SizeType j {0}; j < slots.Size(); ++j) {
                        const int slot {MadJson::getInt(slots[j], "slot")};
                        const std::string current {
                            MadJson::getString(slots[j], "profile")};
                        const std::string rowTitle {slotLabel + " " +
                                                    std::to_string(slot + 1)};
                        auto button = std::make_shared<ButtonComponent>(
                            rowTitle + ":  " + (current.empty() ? "—" : current), rowTitle,
                            [this, rowTitle, options, current, slot] {
                                std::weak_ptr<int> alive {pageAlive()};
                                openChoice(
                                    rowTitle,
                                    "Loads this profile onto the slot. Your profile file "
                                    "is not modified.",
                                    options, current,
                                    [this, alive, slot](const std::string& value) {
                                        if (alive.expired())
                                            return;
                                        // Refresh once from the apply response
                                        // (post-apply truth), not from the pop.
                                        mSuppressChildPopRefresh = true;
                                        const std::string backend {mBackend};
                                        pageRequest(
                                            "profiles.apply_slot",
                                            [backend, slot,
                                             value](MadJson::Writer& writer) {
                                                writer.Key("backend");
                                                writer.String(
                                                    backend.c_str(),
                                                    static_cast<rapidjson::SizeType>(
                                                        backend.length()));
                                                writer.Key("slot");
                                                writer.Int(slot);
                                                writer.Key("profile");
                                                writer.String(
                                                    value.c_str(),
                                                    static_cast<rapidjson::SizeType>(
                                                        value.length()));
                                            },
                                            [this](bool ok,
                                                   const rapidjson::Value& payload) {
                                                const std::string message {
                                                    MadJson::getString(payload, "message",
                                                                       "apply failed")};
                                                footer()->flash(
                                                    message, 5000,
                                                    !ok || message.rfind("⚠", 0) == 0);
                                                refresh();
                                            },
                                            10000);
                                    });
                            });
                        if (slotX > 0.0f && slotX + button->getSize().x > W) {
                            slotX = 0.0f; // Wrap (still one focus row).
                            y += slotLineHeight + smallHeight * 0.2f;
                            slotLineHeight = 0.0f;
                        }
                        button->setPosition(slotX, y);
                        mScroll->addChild(button.get());
                        mWidgets.emplace_back(button);
                        mControls.push_back({Control::Type::Button, button.get(), y,
                                             y + button->getSize().y, slotRowId});
                        slotX += button->getSize().x + slotGap;
                        slotLineHeight = std::max(slotLineHeight, button->getSize().y);
                    }
                    y += slotLineHeight;
                }
                y += smallHeight * 0.3f;
            }
        }
    }

    std::string advanced;
    const rapidjson::Value& advancedArr {MadJson::getMember(result, "advanced")};
    if (advancedArr.IsArray()) {
        for (rapidjson::SizeType i {0}; i < advancedArr.Size(); ++i) {
            if (!advancedArr[i].IsString())
                continue;
            if (!advanced.empty())
                advanced.append(", ");
            advanced.append(advancedArr[i].GetString());
        }
    }
    if (!advanced.empty()) {
        y += smallHeight * 0.3f;
        addText("Advanced (edit controller-policy.toml): " + advanced, FONT_SIZE_MINI,
                MadTheme::color(MadColor::Secondary), 0.0f);
    }

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);

    mBuilt = true;
    if (mControls.empty()) {
        mPanel->refreshHelpPrompts();
        return;
    }
    setFocus(glm::clamp(mFocusCookie, 0, static_cast<int>(mControls.size()) - 1));
    followFocus();
}

void GuiMadPageBackendDetail::setFocus(const int index)
{
    if (mControls.empty())
        return;
    mFocus = glm::clamp(index, 0, static_cast<int>(mControls.size()) - 1);
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (static_cast<int>(i) == mFocus)
            mControls[i].comp->onFocusGained();
        else
            mControls[i].comp->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPageBackendDetail::followFocus()
{
    if (mScroll == nullptr || mControls.empty())
        return;
    const Control& control {mControls[mFocus]};
    // First control: reveal the warn banner / headers above it too.
    mScroll->ensureVisible(mFocus == 0 ? 0.0f : control.top, control.bottom);
}

bool GuiMadPageBackendDetail::input(InputConfig* config, Input input)
{
    if (!mBuilt || mControls.empty())
        return false;

    if (mControls[mFocus].comp->input(config, input)) {
        followFocus();
        return true;
    }

    if (input.value == 0)
        return false;
    const int row {mControls[mFocus].row};
    if (config->isMappedLike("up", input)) {
        const int target {firstOfRow(row - 1)};
        if (target >= 0) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(target);
            followFocus();
        }
        return true;
    }
    if (config->isMappedLike("down", input)) {
        const int target {firstOfRow(row + 1)};
        if (target >= 0) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(target);
            followFocus();
        }
        return true;
    }
    // Left/right walk a multi-button row (chips/steppers consume these first).
    if (config->isMappedLike("left", input)) {
        if (mFocus > 0 && mControls[mFocus - 1].row == row) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(mFocus - 1);
            followFocus();
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mFocus < static_cast<int>(mControls.size()) - 1 &&
            mControls[mFocus + 1].row == row) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(mFocus + 1);
            followFocus();
        }
        return true;
    }
    return false;
}

int GuiMadPageBackendDetail::firstOfRow(const int row) const
{
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (mControls[i].row == row)
            return static_cast<int>(i);
    }
    return -1;
}

void GuiMadPageBackendDetail::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr || mControls.empty())
        return;
    std::vector<PagedTarget> targets;
    for (size_t i {0}; i < mControls.size(); ++i)
        targets.push_back({static_cast<int>(i), -1, mControls[i].top, mControls[i].bottom});
    bool moved {false};
    if (mScroll->overflows())
        moved = mScroll->pageScroll(direction);
    const float viewTop {mScroll->overflows() ? mScroll->scrollOffset() : 0.0f};
    const float viewBottom {viewTop + (mScroll->overflows() ? mScroll->getSize().y :
                                                              mScroll->contentHeight())};
    const int pick {pickPagedTarget(targets, direction, viewTop, viewBottom)};
    if (pick >= 0) {
        if (targets[pick].id != mFocus) {
            setFocus(targets[pick].id);
            moved = true;
        }
        followFocus();
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPageBackendDetail::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt || mControls.empty())
        return prompts;
    prompts.push_back(HelpPrompt("up/down", "choose"));
    switch (mControls[mFocus].type) {
        case Control::Type::Chips: {
            prompts.push_back(HelpPrompt("left/right", "choose"));
            prompts.push_back(HelpPrompt("a", "toggle"));
            break;
        }
        case Control::Type::Stepper: {
            prompts.push_back(HelpPrompt("left/right", "adjust"));
            break;
        }
        case Control::Type::Button: {
            const int row {mControls[mFocus].row};
            const bool multi {(mFocus > 0 && mControls[mFocus - 1].row == row) ||
                              (mFocus < static_cast<int>(mControls.size()) - 1 &&
                               mControls[mFocus + 1].row == row)};
            if (multi)
                prompts.push_back(HelpPrompt("left/right", "choose"));
            prompts.push_back(HelpPrompt("a", "select"));
            break;
        }
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageBackendDetail::onSaveFocus()
{
    mFocusCookie = mFocus;
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageBackendDetail::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocus(mFocusCookie);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}
