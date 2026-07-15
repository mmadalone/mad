//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePergamePads.cpp
//
//  MAD control panel: per-game "Controllers -> pads -> players" reorder (deck-patches).
//  See the header. A trimmed sibling of GuiMadPagePadsPriority (no hands-off; the order is
//  a PER-GAME override keyed by titleid, not the emulator's own config). Data:
//  <ns>.pads_get / <ns>.pads_set_order.
//

#include "guis/mad/pages/GuiMadPagePergamePads.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadPageUtil.h"
#include "guis/mad/MadTheme.h"

#include <cmath>

GuiMadPagePergamePads::GuiMadPagePergamePads(GuiMadPanel* panel, const std::string& title,
                                             const std::string& ns, const std::string& titleid)
    : MadPage {panel, title}
    , mNs {ns}
    , mTitleId {titleid}
    , mFocusTarget {FocusList}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void GuiMadPagePergamePads::build()
{
    setLoadingText("Loading controllers…");
    const std::string ns {mNs};
    const std::string tid {mTitleId};
    pageRequest(
        ns + ".pads_get",
        [tid](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(tid.c_str(), static_cast<rapidjson::SizeType>(tid.length()));
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

void GuiMadPagePergamePads::rebuild(const rapidjson::Value& result)
{
    mIdByLabel.clear();
    mList = nullptr;
    mApplyButton = nullptr;
    mBaselineOrder.clear();
    std::vector<std::string> order;
    MadPageUtil::uniquifyPadLabels(MadJson::getMember(result, "pads"), mIdByLabel, order);

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    // The backend caption (top = Player 1, per-game scope, the two-identical-pads caveat).
    const std::string caption {
        MadJson::getString(result, "caption", MadJson::getString(result, "note", ""))};
    if (!caption.empty()) {
        mNote = std::make_shared<TextComponent>(caption, Font::get(FONT_SIZE_SMALL),
                                                MadTheme::color(MadColor::Secondary), ALIGN_LEFT,
                                                ALIGN_CENTER, glm::ivec2 {0, 1});
        mNote->setPosition(0.0f, y);
        mNote->setSize(mScroll->getSize().x * 0.92f, 0.0f);
        mScroll->addChild(mNote.get());
        y += mNote->getSize().y + smallHeight * 0.5f;
    }

    const bool listShown {!order.empty()};
    if (listShown) {
        mList = std::make_shared<MadReorderList>();
        mList->setPosition(0.0f, y);
        mList->setSize(mViewportSize.x * 0.7f, 1.0f);
        mList->setItems(order);
        mBaselineOrder = order; // clean baseline: the stored per-game order
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
    setFocusTarget(FocusList);
    followFocus();
}

void GuiMadPagePergamePads::apply()
{
    if (mList == nullptr)
        return;
    // Snapshot the order actually sent; the baseline advances to THIS on success,
    // not a fresh mList->items() at reply time (which a reorder during the async
    // window would corrupt, silently clearing dirty).
    const std::vector<std::string> sentOrder {mList->items()};
    const std::vector<std::string> ids {MadPageUtil::labelsToIds(sentOrder, mIdByLabel)};
    const std::string ns {mNs};
    const std::string tid {mTitleId};
    pageRequest(
        ns + ".pads_set_order",
        [tid, ids](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(tid.c_str(), static_cast<rapidjson::SizeType>(tid.length()));
            writer.Key("order");
            writer.StartArray();
            for (const std::string& vp : ids)
                writer.String(vp.c_str(), static_cast<rapidjson::SizeType>(vp.length()));
            writer.EndArray();
        },
        [this, sentOrder](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "couldn't apply"), 4000,
                                true);
                return;
            }
            // Confirmed write: advance the baseline to the order we SENT so dirty
            // clears (the APPLY button and X=Save both land here).
            mBaselineOrder = sentOrder;
            mPanel->refreshHelpPrompts();
            footer()->flash(MadJson::getString(payload, "message", "Applied"));
        },
        10000);
}

bool GuiMadPagePergamePads::isDirty() const
{
    return mBuilt && mList != nullptr && mList->items() != mBaselineOrder;
}

bool GuiMadPagePergamePads::hasUnsavedEdits() const
{
    return isDirty();
}

bool GuiMadPagePergamePads::madSave()
{
    if (!isDirty())
        return false;
    apply(); // queues <ns>.pads_set_order; baseline advances when the reply lands
    return true;
}

bool GuiMadPagePergamePads::madCancel()
{
    if (!isDirty())
        return false;
    build(); // re-fetch: rebuild() resets mList + mBaselineOrder to the stored order
    return true;
}

void GuiMadPagePergamePads::setFocusTarget(const int target)
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

void GuiMadPagePergamePads::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPagePergamePads::followFocus()
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

bool GuiMadPagePergamePads::onBackPressed()
{
    if (mList != nullptr && mList->carrying()) {
        mList->cancelCarry();
        mPanel->refreshHelpPrompts();
        return true;
    }
    return false;
}

bool GuiMadPagePergamePads::input(InputConfig* config, Input input)
{
    if (!mBuilt || mList == nullptr)
        return false;

    if (mFocusTarget == FocusList) {
        if (mList->input(config, input)) {
            followFocus();                // Cursor (or the carried row) moved.
            mPanel->refreshHelpPrompts(); // Carry state changes the prompts.
            return true;
        }
        if (input.value == 0)
            return false;
        if (config->isMappedLike("down", input)) {
            if (!mList->carrying())       // A carry never leaves the list.
                moveFocus(FocusApply);
            return true;
        }
        if (config->isMappedLike("up", input))
            return true;                  // Top edge.
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
        return true;                      // Bottom edge.
    if (config->isMappedTo("a", input))
        return mApplyButton->input(config, input);
    return false;
}

void GuiMadPagePergamePads::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr || !mScroll->overflows())
        return;
    if (mScroll->pageScroll(direction))
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPagePergamePads::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusList && mList != nullptr)
        prompts = mList->getHelpPrompts();
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

void GuiMadPagePergamePads::onSaveFocus()
{
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPagePergamePads::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocusTarget(mFocusTarget);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}
