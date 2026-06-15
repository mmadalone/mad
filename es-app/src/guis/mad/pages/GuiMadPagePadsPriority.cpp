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

#include <cmath>

GuiMadPagePadsPriority::GuiMadPagePadsPriority(GuiMadPanel* panel, const std::string& title,
                                               const std::string& emu)
    : MadPage {panel, title}
    , mEmu {emu}
    , mPlayers {2}
    , mFocusTarget {FocusList}
    , mListCookie {0}
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
    mPlayers = MadJson::getInt(result, "players", 2);
    const bool running {MadJson::getBool(result, "running")};

    mIdByLabel.clear();
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

    if (order.empty()) {
        setLoadingText("No controllers connected — connect a pad, then reopen.");
        mPanel->refreshHelpPrompts();
        return;
    }

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mList = std::make_shared<MadReorderList>();
    mList->setPosition(0.0f, y);
    mList->setSize(mViewportSize.x * 0.7f, 1.0f);
    mList->setItems(order);
    mList->setSize(mViewportSize.x * 0.7f, std::max(1.0f, mList->contentHeight()));
    mScroll->addChild(mList.get());
    y += mList->getSize().y + smallHeight * 0.5f;

    mApplyButton = std::make_shared<ButtonComponent>("APPLY", "apply", [this] { apply(); });
    mApplyButton->setPosition(0.0f, y);
    mScroll->addChild(mApplyButton.get());
    y += mApplyButton->getSize().y;

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);

    mBuilt = true;
    setFocusTarget(FocusList);
    followFocus();

    // The one bit of text worth surfacing: a running emulator can't be written.
    if (running)
        footer()->setStatus(MadJson::getString(result, "note",
                                "Close the emulator first — it rewrites its config on exit."),
                            true);
}

void GuiMadPagePadsPriority::apply()
{
    std::vector<std::string> ids;
    for (const std::string& label : mList->items()) {
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
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "couldn't apply"),
                                4000, true);
                return;
            }
            footer()->flash(MadJson::getString(payload, "message", "Applied") +
                            std::string(" — launch the game to use it."));
        },
        10000);
}

void GuiMadPagePadsPriority::setFocusTarget(const int target)
{
    mFocusTarget = target;
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
    if (mFocusTarget == FocusList && mList != nullptr) {
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
        if (config->isMappedLike("up", input))
            return true;                // Top edge.
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
    else {
        prompts.push_back(HelpPrompt("a", "apply"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPagePadsPriority::onSaveFocus()
{
    if (mList != nullptr)
        mListCookie = mList->cursorIndex();
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
