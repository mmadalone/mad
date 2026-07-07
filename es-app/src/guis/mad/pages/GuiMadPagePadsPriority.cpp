//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePadsPriority.cpp
//
//  MAD control panel: per-emulator "Controllers → pads → players" (deck-patches).
//  See the header. Reorder connected pads (top = Player 1) and Apply → pads.set
//  writes the emulator's own config (configure-once). Modeled on the Priority
//  editor's MadReorderList + Save flow, minus the intro/lightgun/clear extras.
//

#include "guis/mad/pages/GuiMadPagePadsPriority.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

#include <cmath>

GuiMadPagePadsPriority::GuiMadPagePadsPriority(GuiMadPanel* panel, const std::string& title,
                                               const std::string& emu)
    : MadPage {panel, title}
    , mEmu {emu}
    , mFocusTarget {FocusList}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void GuiMadPagePadsPriority::build()
{
    setLoadingText("Loading controllers…");
    const std::string emu {mEmu};
    pageRequest(
        "pads.get",
        [emu](MadJson::Writer& writer) {
            writer.Key("emu");
            writer.String(emu.c_str(), static_cast<rapidjson::SizeType>(emu.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't list controllers: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        },
        10000);
}

void GuiMadPagePadsPriority::rebuild(const rapidjson::Value& result)
{
    const bool running {MadJson::getBool(result, "running")};

    mHandsOff = MadJson::getBool(result, "hands_off", false);

    mIdByLabel.clear();
    mList = nullptr;
    mApplyButton = nullptr;
    mOrderBaseline.clear();
    std::vector<std::string> order;
    const rapidjson::Value& pads {MadJson::getMember(result, "pads")};
    if (pads.IsArray()) {
        for (rapidjson::SizeType i {0}; i < pads.Size(); ++i) {
            const std::string id {MadJson::getString(pads[i], "id")};
            std::string label {MadJson::getString(pads[i], "label")};
            if (label.empty())
                label = id;
            // Keep the reorder-list keys unique even if two pads share a name.
            std::string uniq {label};
            int n {2};
            while (mIdByLabel.count(uniq))
                uniq = label + " (" + std::to_string(n++) + ")";
            mIdByLabel[uniq] = id;
            order.emplace_back(uniq);
        }
    }

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    // Hands-off toggle — always shown, on top. ON = this emulator loads its own
    // controller config (MAD's launch wrapper skips it); OFF = MAD applies the
    // pads → players order below at launch.
    mHandsOffLabel = std::make_shared<TextComponent>(
        "Hands-off", Font::get(FONT_SIZE_MEDIUM),
        mFocusTarget == FocusHandsOff ? MadTheme::color(MadColor::HighlightAccent)
                                      : MadTheme::color(MadColor::Primary),
        ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {1, 0});
    mScroll->addChild(mHandsOffLabel.get());
    mHandsOffSwitch = std::make_shared<SwitchComponent>();
    mHandsOffSwitch->setState(mHandsOff);
    mHandsOffSwitch->setCallback([this] { toggleHandsOff(); });
    mScroll->addChild(mHandsOffSwitch.get());
    // Vertically center the label and the switch within the row: they have
    // different heights, so placing both at the same top-y left them misaligned
    // (the switch sat above the label's text baseline).
    const float rowH {std::max(mHandsOffLabel->getSize().y, mHandsOffSwitch->getSize().y)};
    mHandsOffLabel->setPosition(0.0f, y + (rowH - mHandsOffLabel->getSize().y) * 0.5f);
    mHandsOffSwitch->setPosition(mHandsOffLabel->getSize().x + smallHeight * 0.6f,
                                 y + (rowH - mHandsOffSwitch->getSize().y) * 0.5f);
    y += rowH + smallHeight * 0.4f;

    // Current-mode note: the Hands-off explanation (formerly the button's own
    // label, now that the toggle shows only ON/OFF) plus the backend's own
    // description; wraps within the column.
    const std::string backendNote {MadJson::getString(result, "note")};
    const std::string handsOffNote {
        mHandsOff ? "Hands-off ON — this emulator uses its own controller config."
                  : "Hands-off OFF — MAD applies the pads → players order below at launch."};
    mNote = std::make_shared<TextComponent>(backendNote.empty() ? handsOffNote
                                                                : handsOffNote + "\n" + backendNote,
                                            Font::get(FONT_SIZE_SMALL),
                                            MadTheme::color(MadColor::Secondary),
                                            ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 1});
    mNote->setPosition(0.0f, y);
    mNote->setSize(mScroll->getSize().x * 0.92f, 0.0f);
    mScroll->addChild(mNote.get());
    y += mNote->getSize().y + smallHeight * 0.5f;

    // The reorder list + Apply only when MAD manages this emulator and pads exist.
    const bool listShown {!mHandsOff && !order.empty()};
    if (listShown) {
        mList = std::make_shared<MadReorderList>();
        mList->setPosition(0.0f, y);
        mList->setSize(mViewportSize.x * 0.7f, 1.0f);
        mList->setItems(order);
        mOrderBaseline = order; // clean baseline: the stored pad order
        mList->setSize(mViewportSize.x * 0.7f, std::max(1.0f, mList->contentHeight()));
        mScroll->addChild(mList.get());
        y += mList->getSize().y + smallHeight * 0.5f;

        mApplyButton = std::make_shared<ButtonComponent>("APPLY", "apply", [this] { apply(); });
        mApplyButton->setPosition(0.0f, y);
        mScroll->addChild(mApplyButton.get());
        y += mApplyButton->getSize().y;
    }

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);

    mBuilt = true;
    setFocusTarget(listShown ? FocusList : FocusHandsOff);
    followFocus();

    // The one bit of text worth surfacing: a running emulator can't be written.
    if (running)
        footer()->setStatus(MadJson::getString(result, "note",
                                "Close the emulator first — it rewrites its config on exit."),
                            true);
}

void GuiMadPagePadsPriority::toggleHandsOff()
{
    const std::string emu {mEmu};
    const bool next {!mHandsOff};
    pageRequest(
        "pads.hands_off",
        [emu, next](MadJson::Writer& writer) {
            writer.Key("emu");
            writer.String(emu.c_str(), static_cast<rapidjson::SizeType>(emu.length()));
            writer.Key("value");
            writer.Bool(next);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                // SwitchComponent::input() optimistically flipped the glyph before this
                // async reply arrived; on failure revert it so the switch matches mHandsOff
                // (unchanged) — otherwise the toggle, the note, and the help prompt disagree.
                if (mHandsOffSwitch != nullptr)
                    mHandsOffSwitch->setState(mHandsOff);
                footer()->flash(MadJson::getString(payload, "message", "couldn't change"),
                                4000, true);
                return;
            }
            footer()->flash(MadJson::getString(payload, "message", "Changed"));
            build(); // re-fetch so the list/Apply appear or disappear to match
        });
}

void GuiMadPagePadsPriority::apply()
{
    // Snapshot the order actually sent; the baseline advances to THIS on success,
    // not a fresh mList->items() at reply time (which a reorder during the async
    // window would corrupt, silently clearing dirty).
    const std::vector<std::string> sentOrder {mList->items()};
    std::vector<std::string> ids;
    for (const std::string& label : sentOrder) {
        const auto it = mIdByLabel.find(label);
        if (it != mIdByLabel.end())
            ids.push_back(it->second);
    }
    const std::string emu {mEmu};
    pageRequest(
        "pads.set",
        [emu, ids](MadJson::Writer& writer) {
            writer.Key("emu");
            writer.String(emu.c_str(), static_cast<rapidjson::SizeType>(emu.length()));
            writer.Key("order");
            writer.StartArray();
            for (const std::string& vp : ids)
                writer.String(vp.c_str(), static_cast<rapidjson::SizeType>(vp.length()));
            writer.EndArray();
        },
        [this, sentOrder](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "couldn't apply"),
                                4000, true);
                return;
            }
            // Confirmed write: advance the baseline to the order we SENT so dirty
            // clears (APPLY and X=Save both land here).
            mOrderBaseline = sentOrder;
            mPanel->refreshHelpPrompts();
            footer()->flash(MadJson::getString(payload, "message", "Applied") +
                            std::string(" — launch the game to use it."));
        },
        10000);
}

bool GuiMadPagePadsPriority::isDirty() const
{
    return mBuilt && mList != nullptr && mList->items() != mOrderBaseline;
}

bool GuiMadPagePadsPriority::hasUnsavedEdits() const
{
    return isDirty();
}

bool GuiMadPagePadsPriority::madSave()
{
    if (!isDirty())
        return false;
    apply(); // queues pads.set; baseline advances when the reply lands
    return true;
}

bool GuiMadPagePadsPriority::madCancel()
{
    if (!isDirty())
        return false;
    build(); // re-fetch: rebuild() resets mList + mOrderBaseline to the stored order
    return true;
}

void GuiMadPagePadsPriority::setFocusTarget(const int target)
{
    mFocusTarget = target;
    if (mHandsOffLabel != nullptr)
        mHandsOffLabel->setColor(target == FocusHandsOff ? MadTheme::color(MadColor::HighlightAccent)
                                                         : MadTheme::color(MadColor::Primary));
    if (mList != nullptr) {
        if (target == FocusList)
            mList->onFocusGained();
        else
            mList->onFocusLost();
    }
    if (mApplyButton != nullptr) {
        if (target == FocusApply)
            mApplyButton->onFocusGained();
        else
            mApplyButton->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void GuiMadPagePadsPriority::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPagePadsPriority::followFocus()
{
    if (mScroll == nullptr)
        return;
    float top {0.0f};
    float bottom {0.0f};
    if (mFocusTarget == FocusHandsOff && mHandsOffLabel != nullptr) {
        top = mHandsOffLabel->getPosition().y;
        bottom = top + mHandsOffLabel->getSize().y;
    }
    else if (mFocusTarget == FocusList && mList != nullptr) {
        const glm::vec2 row {mList->cursorRowRect()};
        top = mList->getPosition().y + row.x;
        bottom = mList->getPosition().y + row.y;
    }
    else if (mApplyButton != nullptr) {
        top = mApplyButton->getPosition().y;
        bottom = top + mApplyButton->getSize().y;
    }
    mScroll->ensureVisible(top, bottom);
}

bool GuiMadPagePadsPriority::onBackPressed()
{
    if (mList != nullptr && mList->carrying()) {
        mList->cancelCarry();
        mPanel->refreshHelpPrompts();
        return true;
    }
    return false;
}

bool GuiMadPagePadsPriority::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusHandsOff) {
        if (input.value == 0)
            return false;
        if (config->isMappedTo("a", input))
            return mHandsOffSwitch->input(config, input); // toggles → fires toggleHandsOff()
        if (config->isMappedLike("down", input)) {
            if (mList != nullptr)       // only when MAD manages this emulator
                moveFocus(FocusList);
            return true;
        }
        if (config->isMappedLike("up", input))
            return true;                // Top edge.
        return false;
    }

    if (mFocusTarget == FocusList) {
        if (mList->input(config, input)) {
            followFocus();              // Cursor (or the carried row) moved.
            mPanel->refreshHelpPrompts(); // Carry state changes the prompts.
            return true;
        }
        if (input.value == 0)
            return false;
        if (config->isMappedLike("down", input)) {
            if (!mList->carrying())     // A carry never leaves the list.
                moveFocus(FocusApply);
            return true;
        }
        if (config->isMappedLike("up", input)) {
            if (!mList->carrying())     // Up leaves the list to the Hands-off toggle.
                moveFocus(FocusHandsOff);
            return true;
        }
        return false;
    }

    // Apply button focused.
    if (input.value == 0)
        return false;
    if (config->isMappedLike("up", input)) {
        moveFocus(FocusList);
        return true;
    }
    if (config->isMappedLike("down", input))
        return true;                    // Bottom edge.
    if (config->isMappedTo("a", input))
        return mApplyButton->input(config, input);
    return false;
}

void GuiMadPagePadsPriority::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr || !mScroll->overflows())
        return;
    if (mScroll->pageScroll(direction))
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPagePadsPriority::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusList && mList != nullptr) {
        prompts = mList->getHelpPrompts();
    }
    else if (mFocusTarget == FocusHandsOff) {
        prompts.push_back(HelpPrompt("a", mHandsOff ? "let MAD manage" : "hands-off"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    else {
        prompts.push_back(HelpPrompt("a", "apply"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    if (isDirty()) {
        prompts.push_back(HelpPrompt("x", "save"));
        prompts.push_back(HelpPrompt("y", "cancel"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPagePadsPriority::onSaveFocus()
{
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPagePadsPriority::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocusTarget(mFocusTarget);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}
