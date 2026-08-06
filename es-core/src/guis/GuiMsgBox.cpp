//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMsgBox.cpp
//
//  Popup message dialog with a notification text and a choice of one,
//  two or three buttons.
//

#include "guis/GuiMsgBox.h"

#include "components/ButtonComponent.h"
#include "components/MenuComponent.h"

#include <algorithm> // deck-patches: std::max in the layout passes

#define HORIZONTAL_PADDING_PX 20.0f
#define VERTICAL_PADDING_MODIFIER 1.225f

GuiMsgBox::GuiMsgBox(const std::string& text,
                     const std::string& name1,
                     const std::function<void()>& func1,
                     const std::string& name2,
                     const std::function<void()>& func2,
                     const std::string& name3,
                     const std::function<void()>& func3,
                     const std::string& name4,
                     const std::function<void()>& func4,
                     const std::function<void()>& backFunc,
                     const bool disableBackButton,
                     const bool deleteOnButtonPress,
                     const float maxWidthMultiplier)
    : mRenderer {Renderer::getInstance()}
    , mGrid {glm::ivec2 {1, 2}}
    , mBackFunc {backFunc}
    , mDisableBackButton {disableBackButton}
    , mDeleteOnButtonPress {deleteOnButtonPress}
    , mMaxWidthMultiplierRequested {maxWidthMultiplier}
    , mMaxWidthMultiplier {maxWidthMultiplier}
{
    // Initially set the text component to wrap by line breaks while maintaining the row lengths.
    // This is the "ideal" size for the text as it's exactly how it's written.
    mMsg = std::make_shared<TextComponent>(text, Font::get(FONT_SIZE_MEDIUM), mMenuColorPrimary,
                                           ALIGN_CENTER, ALIGN_CENTER, glm::ivec2 {1, 1});
    mGrid.setEntry(mMsg, glm::ivec2 {0, 0}, false, false);

    // Create the buttons.
    mButtons.push_back(std::make_shared<ButtonComponent>(
        name1, name1, std::bind(&GuiMsgBox::deleteMeAndCall, this, func1)));
    if (!name2.empty())
        mButtons.push_back(std::make_shared<ButtonComponent>(
            name2, name2, std::bind(&GuiMsgBox::deleteMeAndCall, this, func2)));
    if (!name3.empty())
        mButtons.push_back(std::make_shared<ButtonComponent>(
            name3, name3, std::bind(&GuiMsgBox::deleteMeAndCall, this, func3)));
    if (!name4.empty())
        mButtons.push_back(std::make_shared<ButtonComponent>(
            name4, name4, std::bind(&GuiMsgBox::deleteMeAndCall, this, func4)));

    // Put the buttons into a ComponentGrid.
    mButtonGrid = MenuComponent::makeButtonGrid(mButtons);
    mGrid.setEntry(mButtonGrid, glm::ivec2 {0, 1}, true, false, glm::ivec2 {1, 1},
                   GridFlags::BORDER_TOP);

    calculateSize();

    setPosition((mRenderer->getScreenWidth() - mSize.x) / 2.0f,
                (mRenderer->getScreenHeight() - mSize.y) / 2.0f);

    addChild(&mBackground);
    addChild(&mGrid);
}

// deck-patches: one layout attempt at the CURRENT font. Returns the chosen width and leaves
// mMsg wrapped to it.
float GuiMsgBox::layoutPass(float aspectValue, float& naturalWidth)
{
    // Re-measure the text at its natural size first: the width decision below compares
    // against it, and after a previous pass mMsg is wrapped rather than natural. Re-setting
    // the text is what forces the cache to recompute, the same trick changeText() uses.
    mMsg->setAutoCalcExtent(glm::vec2 {1, 1});
    mMsg->setText(mMsg->getValue());
    // The width of the longest line AS WRITTEN. Compared against the final width by the
    // caller to tell "this text wraps" from "this text fits".
    naturalWidth = mMsg->getSize().x;

    mMaxWidthMultiplier = mMaxWidthMultiplierRequested;
    if (mMaxWidthMultiplier == 0.0f)
        mMaxWidthMultiplier = mRenderer->getIsVerticalOrientation() ? 0.90f : 0.80f;

    // A message that did not fit gets the screen it needs.
    //
    // BOTH numbers have to move. Raising only the ceiling does nothing on a 16:10 Deck,
    // because the base term decides the result there: 0.60 * (1.778/1.6) = 0.667, so the
    // box stopped at 67% of the screen no matter how high the cap was set. Measured on a
    // real screenshot, not assumed - the first attempt raised the ceiling alone and left a
    // third of the screen unused.
    const float baseMultiplier {mLongMessage ? 0.86f : 0.60f};
    if (mLongMessage)
        mMaxWidthMultiplier = std::max(mMaxWidthMultiplier, 0.92f);

    float width {std::floor(glm::clamp(baseMultiplier * aspectValue, 0.60f,
                                       mMaxWidthMultiplier) *
                            mRenderer->getScreenWidth())};
    const float minWidth {
        floorf(glm::clamp(0.30f * aspectValue, 0.10f, 0.50f) * mRenderer->getScreenWidth())};

    // Decide final width.
    if (mLongMessage) {
        // The shrink-to-fit branch below is for short confirms, where a narrow box around a
        // short string looks deliberate. Applying it to text that overflows is what produced
        // a box half the screen wide with every line wrapped inside it.
        width = std::max(width, mButtonGrid->getSize().x);
    }
    else if (mMsg->getSize().x < width && mButtonGrid->getSize().x < width) {
        // mMsg and buttons are narrower than width.
        width = std::max(mButtonGrid->getSize().x, mMsg->getSize().x);
        width = std::max(width, minWidth);
    }
    else if (mButtonGrid->getSize().x > width) {
        width = mButtonGrid->getSize().x;
    }

    // As the actual rows may be too wide to fit we change to wrapping by our component width
    // while allowing expansion vertically. Setting the width will update the text cache.
    mMsg->setAutoCalcExtent(glm::vec2 {0, 1});
    mMsg->setSize(width, 0.0f);

    return width;
}

float GuiMsgBox::measuredMsgHeight() const
{
    return std::max(Font::get(FONT_SIZE_LARGE)->getHeight(),
                    mMsg->getSize().y * VERTICAL_PADDING_MODIFIER);
}

void GuiMsgBox::calculateSize()
{
    // Adjust the width relative to the aspect ratio of the screen to make the GUI look coherent
    // regardless of screen type. The 1.778 aspect ratio value is the 16:9 reference.
    const float aspectValue {1.778f / mRenderer->getScreenAspectRatio()};

    // deck-patches: escalate ONLY as far as the content requires, deciding on RENDERED HEIGHT
    // rather than on how the text happens to be punctuated. An earlier version keyed off the
    // newline count and misjudged every hand-wrapped short confirm in the app - the kiosk/kid
    // mode notice is six short lines and would have been shrunk and widened for no reason.
    //
    // Nothing bounded the height before this, so a message taller than the screen pushed its
    // own buttons off the bottom; with MadMsgBox swallowing the keyboard that dialog could not
    // be answered at all.
    const float availableForMsg {mRenderer->getScreenHeight() * 0.85f -
                                 mButtonGrid->getSize().y};

    // PASS 1 - the original layout, unchanged. Reset everything a previous pass may have
    // altered, so a changeText() that SHORTENS the message returns to the normal look
    // instead of staying wide and small.
    mLongMessage = false;
    mMsg->setFont(Font::get(FONT_SIZE_MEDIUM));
    float naturalWidth {0.0f};
    float width {layoutPass(aspectValue, naturalWidth)};
    float msgHeight {measuredMsgHeight()};

    // TWO independent reasons to escalate, and the width one is why this patch exists.
    //
    // TOO TALL is the safety problem: the buttons walk off the bottom.
    // TOO WIDE is the reported one: the changelog fit vertically but had to wrap inside a
    // box using half the screen, so it read as broken. Escalating on height alone would
    // leave that exact dialog untouched.
    //
    // "Wraps" means the longest line as written does not fit the chosen width. A short
    // hand-wrapped confirm never trips it - its lines are already short, so shrink-to-fit
    // gives them their natural width and nothing wraps. That is what keeps upstream's
    // kiosk/kid notice and the backup-delete confirm on the original path.
    const bool wrapsAtThisWidth {naturalWidth > width + 1.0f};

    if (availableForMsg > 0.0f && (msgHeight > availableForMsg || wrapsAtThisWidth)) {
        // PASS 2 - smaller font and the full width. On a 16:10 screen this alone absorbs a
        // lot of text, so try it before reaching for a scrollbar.
        mLongMessage = true;
        mMsg->setFont(Font::get(FONT_SIZE_SMALL));
        width = layoutPass(aspectValue, naturalWidth);
        msgHeight = measuredMsgHeight();

        if (msgHeight > availableForMsg) {
            // PASS 3 - still too tall: clamp and scroll. ScrollableContainer is the component
            // the gamelist already uses for descriptions, which this fork taught to drag with
            // a finger.
            msgHeight = availableForMsg;
            if (mMsgScroll == nullptr) {
                // Take the text OUT of the grid FIRST. ComponentGrid::setEntry only ever
                // appends, so leaving the old cell behind would keep the grid repositioning
                // mMsg in GRID coordinates after it became a child of the container - which
                // parks the first lines above the clip rect where they cannot be scrolled to.
                // The removal must happen while mGrid is still the parent.
                mGrid.removeEntry(mMsg);
                mMsgScroll = std::make_shared<ScrollableContainer>();
                mMsgScroll->addChild(mMsg.get());
                mGrid.setEntry(mMsgScroll, glm::ivec2 {0, 0}, false, false);
            }
            mMsgScroll->setSize(width, msgHeight);
            mMsg->setPosition(0.0f, 0.0f, 0.0f); // origin of the CONTAINER, not the grid
            // Auto-scroll rather than a hidden binding: the buttons own the d-pad here, so a
            // manual scroll control would be undiscoverable.
            mMsgScroll->setAutoScroll(true);
        }
    }

    setSize(std::round(width + std::ceil(HORIZONTAL_PADDING_PX * 2.0f *
                                         mRenderer->getScreenWidthModifier())),
            std::round(msgHeight + mButtonGrid->getSize().y));
}

void GuiMsgBox::changeText(const std::string& newText)
{
    mMsg->setAutoCalcExtent(glm::vec2 {1, 1});
    mMsg->setText(newText);

    calculateSize();
}

bool GuiMsgBox::input(InputConfig* config, Input input)
{
    if (!mDisableBackButton && config->isMappedTo("b", input) && input.value != 0) {
        if (mBackFunc)
            mBackFunc();
        delete this;
        return true;
    }

    return GuiComponent::input(config, input);
}

void GuiMsgBox::onSizeChanged()
{
    mGrid.setSize(mSize);
    mGrid.setRowHeightPerc(1, mButtonGrid->getSize().y / mSize.y);

    const float innerWidth {
        mSize.x - std::ceil(HORIZONTAL_PADDING_PX * 2.0f * Renderer::getScreenWidthModifier())};

    if (mMsgScroll != nullptr) {
        // deck-patches: the CONTAINER takes the row height and clips; the text keeps its own
        // full height so there is something to scroll. Forcing the text to the row height (as
        // the unscrolled path does) would squash it and leave nothing to move.
        mMsgScroll->setSize(innerWidth, mGrid.getRowHeight(0));
        mMsg->setSize(innerWidth, 0.0f);
    }
    else {
        mMsg->setSize(innerWidth, mGrid.getRowHeight(0));
    }
    mGrid.onSizeChanged();

    mBackground.fitTo(mSize);
}

void GuiMsgBox::deleteMeAndCall(const std::function<void()>& func)
{
    auto funcCopy = func;

    if (mDeleteOnButtonPress)
        delete this;

    if (funcCopy)
        funcCopy();
}

std::vector<HelpPrompt> GuiMsgBox::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {mGrid.getHelpPrompts()};

    if (!mDisableBackButton)
        prompts.push_back(HelpPrompt("b", _("back")));

    return prompts;
}
