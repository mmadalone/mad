//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRAControllers.cpp
//
//  MAD control panel: RetroArch hub -> Controllers section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageRAControllers.h"

#include "Sound.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPagePriority.h" // The per-system/collection rules subpage.

#include <algorithm>

namespace
{
    std::string joinComma(const std::vector<std::string>& items)
    {
        std::string out;
        for (size_t i {0}; i < items.size(); ++i) {
            if (i > 0)
                out += ", ";
            out += items[i];
        }
        return out;
    }
} // namespace

GuiMadPageRAControllers::GuiMadPageRAControllers(GuiMadPanel* panel, const std::string& title)
    : MadPage {panel, title}
    , mNports {2}
    , mFocusTarget {FocusReorderList}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void GuiMadPageRAControllers::build()
{
    setLoadingText("Loading RetroArch controllers…");
    pageRequest(
        "racontrollers.get",
        [](MadJson::Writer& writer) {
            writer.Key("scope");
            writer.String("global", 6);
            writer.Key("name");
            writer.String("", 0);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load RetroArch controllers: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mGlobalOrder.clear();
            const rapidjson::Value& orderArr {MadJson::getMember(payload, "order")};
            if (orderArr.IsArray()) {
                for (rapidjson::SizeType i {0}; i < orderArr.Size(); ++i)
                    if (orderArr[i].IsString())
                        mGlobalOrder.emplace_back(orderArr[i].GetString());
            }
            mNports = MadJson::getInt(payload, "nports", 2);
            mConnectedFamilies.clear();
            const rapidjson::Value& connArr {MadJson::getMember(payload, "connected_families")};
            if (connArr.IsArray()) {
                for (rapidjson::SizeType i {0}; i < connArr.Size(); ++i)
                    if (connArr[i].IsString())
                        mConnectedFamilies.emplace_back(connArr[i].GetString());
            }
            rebuild();
        },
        10000);
}

void GuiMadPageRAControllers::onChildPopped()
{
    build(); // Harmless refresh of the global order/connected line after the subpage.
}

void GuiMadPageRAControllers::rebuild()
{
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    // Children first (dtors self-detach), then the scroll view.
    mIntro.reset();
    mConnectedLine.reset();
    mHint.reset();
    mGlobalList.reset();
    mSaveButton.reset();
    mClearButton.reset();
    mSubpageButton.reset();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }

    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());

    float y {0.0f};

    mIntro = std::make_shared<TextComponent>(
        "Preferred controller per system (top = Player 1). RetroArch systems only; "
        "standalone emulators are configured on the Backends page. A custom COLLECTION rule "
        "overrides the system rule for its member games (e.g. a lightgun collection).",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(0.0f, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.4f;

    mConnectedLine = std::make_shared<TextComponent>(
        "Connected: " +
            (mConnectedFamilies.empty() ? std::string("(none)") : joinComma(mConnectedFamilies)),
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mConnectedLine->setPosition(0.0f, y);
    mConnectedLine->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mConnectedLine.get());
    y += mConnectedLine->getSize().y + smallHeight * 0.4f;

    bool hasXArcade {false};
    for (const std::string& fam : mGlobalOrder)
        if (fam == "X-Arcade")
            hasXArcade = true;
    std::string hintText {"Reorder the families below (top = Player 1): A lifts a row, up/down "
                          "move it, A drops it. Then Save."};
    if (hasXArcade)
        hintText += "  Note: the X-Arcade is ONE device that fills BOTH Player 1 and Player 2 "
                    "(its two halves), so put it at the top and P1+P2 are both covered; the "
                    "family below it is only used when no X-Arcade is connected.";
    mHint = std::make_shared<TextComponent>(
        hintText,
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mHint->setPosition(0.0f, y);
    mHint->setSize(mViewportSize.x, 0.0f);
    mScroll->addChild(mHint.get());
    y += mHint->getSize().y + smallHeight * 0.4f;

    mGlobalList = std::make_shared<MadReorderList>();
    mGlobalList->setPosition(0.0f, y);
    mGlobalList->setSize(mViewportSize.x * 0.6f, 1.0f);
    mGlobalList->setItems(mGlobalOrder);
    mGlobalList->setSize(mViewportSize.x * 0.6f, std::max(1.0f, mGlobalList->contentHeight()));
    mScroll->addChild(mGlobalList.get());
    y += mGlobalList->getSize().y + smallHeight * 0.5f;

    mSaveButton = std::make_shared<ButtonComponent>("SAVE", "save", [this] { saveGlobalOrder(); });
    mSaveButton->setPosition(0.0f, y);
    mScroll->addChild(mSaveButton.get());
    mClearButton = std::make_shared<ButtonComponent>("CLEAR RULE", "clear rule",
                                                     [this] { clearGlobalOrder(); });
    mClearButton->setPosition(mSaveButton->getSize().x + mViewportSize.x * 0.012f, y);
    mScroll->addChild(mClearButton.get());
    y += mSaveButton->getSize().y + smallHeight * 0.6f;

    mSubpageButton = std::make_shared<ButtonComponent>(
        "PER-SYSTEM & COLLECTION RULES", "per-system & collection rules",
        [this] { mPanel->pushPage(new GuiMadPagePriority(mPanel)); });
    mSubpageButton->setPosition(0.0f, y);
    mScroll->addChild(mSubpageButton.get());
    y += mSubpageButton->getSize().y;

    mScroll->setContentHeight(y + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);

    mBuilt = true;
    setFocusTarget(mFocusTarget);
    followFocus();
}

int GuiMadPageRAControllers::nextTarget(int target, const int direction) const
{
    target += direction;
    if (target < FocusReorderList || target > FocusSubpage)
        return -1;
    return target;
}

void GuiMadPageRAControllers::setFocusTarget(const int target)
{
    mFocusTarget = target;
    if (mGlobalList != nullptr) {
        if (target == FocusReorderList)
            mGlobalList->onFocusGained();
        else
            mGlobalList->onFocusLost();
    }
    auto applyButton = [target](const std::shared_ptr<ButtonComponent>& button,
                                const int focusId) {
        if (button == nullptr)
            return;
        if (target == focusId)
            button->onFocusGained();
        else
            button->onFocusLost();
    };
    applyButton(mSaveButton, FocusSave);
    applyButton(mClearButton, FocusClear);
    applyButton(mSubpageButton, FocusSubpage);
    mPanel->refreshHelpPrompts();
}

void GuiMadPageRAControllers::moveFocus(const int target)
{
    setFocusTarget(target);
    followFocus();
}

void GuiMadPageRAControllers::followFocus()
{
    if (mScroll == nullptr)
        return;
    float top {0.0f};
    float bottom {0.0f};
    switch (mFocusTarget) {
        case FocusReorderList: {
            // Topmost focusable: reveal the intro/connected line above it too,
            // but only when the cursor is on row 0 (a lower row must follow the
            // cursor, not jerk the view up).
            const glm::vec2 row {mGlobalList->cursorRowRect()};
            const bool revealTop {mGlobalList->cursorIndex() == 0};
            top = revealTop ? 0.0f : mGlobalList->getPosition().y + row.x;
            bottom = mGlobalList->getPosition().y + row.y;
            break;
        }
        case FocusSave:
        case FocusClear: {
            top = mSaveButton->getPosition().y;
            bottom = top + mSaveButton->getSize().y;
            break;
        }
        case FocusSubpage: {
            top = mSubpageButton->getPosition().y;
            bottom = top + mSubpageButton->getSize().y;
            break;
        }
        default:
            return;
    }
    mScroll->ensureVisible(top, bottom);
}

void GuiMadPageRAControllers::saveGlobalOrder()
{
    const std::vector<std::string> order {mGlobalList->items()};
    const int nports {mNports};
    pageRequest(
        "policy.set_ports",
        [order, nports](MadJson::Writer& writer) {
            writer.Key("kind");
            writer.String("global", 6);
            writer.Key("name");
            writer.String("", 0);
            writer.Key("order");
            writer.StartArray();
            for (const std::string& family : order)
                writer.String(family.c_str(),
                              static_cast<rapidjson::SizeType>(family.length()));
            writer.EndArray();
            writer.Key("nports");
            writer.Int(nports);
        },
        [this, order](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save the global order: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            // The on-screen order already equals what was saved, so (unlike
            // clear) no rebuild is needed: just confirm, like the Priority editor.
            footer()->flash("Saved the global order: P1 = " +
                            (order.empty() ? "(empty)" : order[0]) +
                            ". Applies on the next game launch (no ES-DE restart).");
        });
}

void GuiMadPageRAControllers::clearGlobalOrder()
{
    pageRequest(
        "policy.clear_ports",
        [](MadJson::Writer& writer) {
            writer.Key("kind");
            writer.String("global", 6);
            writer.Key("name");
            writer.String("", 0);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't clear the global order: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash("Global order cleared, the default order applies");
            build(); // Re-fetch: the order reverts to the family default.
        });
}

bool GuiMadPageRAControllers::onBackPressed()
{
    if (mGlobalList != nullptr && mGlobalList->carrying()) {
        mGlobalList->cancelCarry();
        mPanel->refreshHelpPrompts();
        return true;
    }
    return false;
}

bool GuiMadPageRAControllers::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusReorderList) {
        if (mGlobalList->input(config, input)) {
            followFocus(); // Cursor (or the carried row) moved.
            mPanel->refreshHelpPrompts(); // Carry state changes the prompts.
            return true;
        }
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            if (!mGlobalList->carrying()) {
                const int target {nextTarget(FocusReorderList, -1)}; // -1 at the top: stay put.
                if (target >= 0) {
                    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                    moveFocus(target);
                }
            }
            return true;
        }
        if (config->isMappedLike("down", input)) {
            if (!mGlobalList->carrying()) {
                const int target {nextTarget(FocusReorderList, 1)};
                if (target >= 0) {
                    NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                    moveFocus(target);
                }
            }
            return true;
        }
        return false;
    }

    if (mFocusTarget == FocusSave || mFocusTarget == FocusClear) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("left", input)) {
            if (mFocusTarget == FocusClear) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                setFocusTarget(FocusSave);
            }
            return true;
        }
        if (config->isMappedLike("right", input)) {
            if (mFocusTarget == FocusSave) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                setFocusTarget(FocusClear);
            }
            return true;
        }
        if (config->isMappedLike("up", input)) {
            const int target {nextTarget(FocusSave, -1)}; // Save is the row's low index.
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        if (config->isMappedLike("down", input)) {
            // Anchor on the LAST member of the Save/Clear pair so DOWN descends
            // PAST it onto the subpage button (nextTarget(FocusSave,1) would
            // just return FocusClear, leaving the button unreachable).
            const int target {nextTarget(FocusClear, 1)};
            if (target >= 0) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                moveFocus(target);
            }
            return true;
        }
        if (config->isMappedTo("a", input)) {
            return mFocusTarget == FocusSave ? mSaveButton->input(config, input) :
                                               mClearButton->input(config, input);
        }
        return false;
    }

    // FocusSubpage: the last target, A pushes the subpage, down does nothing.
    if (input.value == 0)
        return false;
    if (config->isMappedLike("up", input)) {
        int target {nextTarget(FocusSubpage, -1)};
        // Entering the Save/Clear pair from below lands on the primary (Save),
        // not whichever button nextTarget's index math happens to reach.
        if (target == FocusClear)
            target = FocusSave;
        if (target >= 0) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            moveFocus(target);
        }
        return true;
    }
    if (config->isMappedLike("down", input))
        return true; // Bottom edge.
    if (config->isMappedTo("a", input))
        return mSubpageButton->input(config, input);
    return false;
}

void GuiMadPageRAControllers::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr)
        return;
    // A live reorder carry owns up/down (they move the carried row); paging away
    // would leave the carry live but invisible. Consume the page input while
    // carrying so the user must drop (A) or cancel (B) first.
    if (mGlobalList != nullptr && mGlobalList->carrying())
        return;
    std::vector<PagedTarget> targets;
    targets.push_back({FocusReorderList, -1, mGlobalList->getPosition().y,
                       mGlobalList->getPosition().y + mGlobalList->getSize().y});
    targets.push_back({FocusSave, -1, mSaveButton->getPosition().y,
                       mSaveButton->getPosition().y + mSaveButton->getSize().y});
    targets.push_back({FocusSubpage, -1, mSubpageButton->getPosition().y,
                       mSubpageButton->getPosition().y + mSubpageButton->getSize().y});

    bool moved {false};
    if (mScroll->overflows())
        moved = mScroll->pageScroll(direction);
    const float viewTop {mScroll->overflows() ? mScroll->scrollOffset() : 0.0f};
    const float viewBottom {viewTop + (mScroll->overflows() ? mScroll->getSize().y :
                                                              mScroll->contentHeight())};
    const int pick {pickPagedTarget(targets, direction, viewTop, viewBottom)};
    if (pick >= 0) {
        const PagedTarget& target {targets[pick]};
        const bool changed {target.id != mFocusTarget};
        setFocusTarget(target.id);
        followFocus();
        if (changed)
            moved = true;
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> GuiMadPageRAControllers::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusReorderList && mGlobalList != nullptr)
        prompts = mGlobalList->getHelpPrompts();
    else if (mFocusTarget == FocusSave || mFocusTarget == FocusClear) {
        prompts.push_back(HelpPrompt("left/right", "choose"));
        prompts.push_back(HelpPrompt("a", "select"));
        prompts.push_back(HelpPrompt("up/down", "choose"));
    }
    else {
        prompts.push_back(HelpPrompt("up/down", "choose"));
        prompts.push_back(HelpPrompt("a", "select"));
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void GuiMadPageRAControllers::onSaveFocus()
{
    mFocusCookie = mFocusTarget;
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void GuiMadPageRAControllers::onRestoreFocus()
{
    if (!mBuilt)
        return;
    mFocusTarget = mFocusCookie;
    // rebuild() (triggered by onChildPopped right after) re-applies the rest.
}
