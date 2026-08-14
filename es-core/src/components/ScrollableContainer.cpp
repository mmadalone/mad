//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  ScrollableContainer.cpp
//
//  Component containing scrollable information, used for the game
//  description text in the scraper and gamelist views.
//

#include "components/ScrollableContainer.h"

#include "Window.h"
#include "animations/LambdaAnimation.h"
#include "renderers/Renderer.h"
#include "resources/Font.h"

ScrollableContainer::ScrollableContainer()
    : mRenderer {Renderer::getInstance()}
    , mScrollPos {0.0f, 0.0f}
    , mScrollDir {0.0f, 0.0f}
    , mAdjustedHeight {0.0f}
    , mClipSpacing {0.0f}
    , mAutoScrollDelay {0}
    , mAutoScrollSpeed {0}
    , mAutoScrollAccumulator {0}
    , mAutoScrollResetAccumulator {0}
    , mAdjustedAutoScrollSpeed {0}
    , mVerticalSnap {true}
    , mUpdatedSize {false}
{
    // Set the modifier to get equivalent scrolling speed regardless of screen resolution.
    mResolutionModifier = mRenderer->getScreenResolutionModifier();

    mAutoScrollResetDelayConstant = AUTO_SCROLL_RESET_DELAY;
    mAutoScrollDelayConstant = AUTO_SCROLL_DELAY;
    mAutoScrollSpeedConstant = AUTO_SCROLL_SPEED;
}

void ScrollableContainer::setAutoScroll(bool autoScroll)
{
    if (autoScroll) {
        mScrollDir = glm::vec2 {0.0f, 1.0f};
        mAutoScrollDelay = static_cast<int>(mAutoScrollDelayConstant);
        resetComponent();
    }
    else {
        mScrollDir = glm::vec2 {};
        mAutoScrollDelay = 0;
        mAutoScrollSpeed = 0;
        mAutoScrollAccumulator = 0;
    }
}

void ScrollableContainer::setScrollParameters(float autoScrollDelayConstant,
                                              float autoScrollResetDelayConstant,
                                              float autoScrollSpeedConstant)
{
    mAutoScrollResetDelayConstant = glm::clamp(autoScrollResetDelayConstant, 1000.0f, 10000.0f);
    mAutoScrollDelayConstant = glm::clamp(autoScrollDelayConstant, 1000.0f, 10000.0f);
    mAutoScrollSpeedConstant = AUTO_SCROLL_SPEED / glm::clamp(autoScrollSpeedConstant, 0.1f, 10.0f);
}

void ScrollableContainer::resetComponent()
{
    mScrollPos = glm::vec2 {0.0f, 0.0f};
    mAutoScrollResetAccumulator = 0;
    mAutoScrollAccumulator = -mAutoScrollDelay + mAutoScrollSpeed;
    mAtEnd = false;
    mUpdatedSize = false;
    mManualScrolled = false; // next game gets its auto-scroll cycle back

    // This applies to the actual TextComponent that is getting displayed.
    mChildren.front()->setAutoCalcExtent(glm::ivec2 {0, 1});

    // This is needed to resize to the designated area when the background image gets invalidated.
    if (!mChildren.empty()) {
        float combinedHeight {0.0f};
        const float cacheGlyphHeight {
            mChildren.front()->getTextCache() == nullptr ?
                0.0f :
                static_cast<float>(mChildren.front()->getTextCache()->metrics.maxGlyphHeight)};

        if (cacheGlyphHeight > 0.0f)
            combinedHeight = cacheGlyphHeight * mChildren.front()->getLineSpacing();
        else
            return;

        if (mChildren.front()->getSize().y > mSize.y) {
            if (mVerticalSnap) {
                float numLines {std::floor(mSize.y / combinedHeight)};
                if (numLines == 0)
                    numLines = 1;
                mAdjustedHeight = std::ceil(numLines * combinedHeight);
            }
            else {
                mAdjustedHeight = mSize.y;
            }
        }
    }
}

void ScrollableContainer::applyTheme(const std::shared_ptr<ThemeData>& theme,
                                     const std::string& view,
                                     const std::string& element,
                                     unsigned int properties)
{
    using namespace ThemeFlags;
    GuiComponent::applyTheme(theme, view, element, properties);

    const ThemeData::ThemeElement* elem {theme->getElement(view, element, "text")};
    if (!elem)
        return;

    if (elem->has("containerVerticalSnap"))
        mVerticalSnap = elem->get<bool>("containerVerticalSnap");

    if (elem->has("containerScrollSpeed")) {
        mAutoScrollSpeedConstant =
            AUTO_SCROLL_SPEED / glm::clamp(elem->get<float>("containerScrollSpeed"), 0.1f, 10.0f);
    }

    if (elem->has("containerStartDelay")) {
        mAutoScrollDelayConstant =
            glm::clamp(elem->get<float>("containerStartDelay"), 0.0f, 10.0f) * 1000.0f;
    }

    if (elem->has("containerResetDelay")) {
        mAutoScrollResetDelayConstant =
            glm::clamp(elem->get<float>("containerResetDelay"), 0.0f, 20.0f) * 1000.0f;
    }
}

void ScrollableContainer::update(int deltaTime)
{
    if (!isVisible() || mSize == glm::vec2 {0.0f, 0.0f})
        return;

    const glm::vec2 contentSize {glm::round(mChildren.front()->getSize())};
    float rowModifier {1.0f};

    const float lineSpacing {mChildren.front()->getLineSpacing()};
    float combinedHeight {0.0f};

    const float cacheGlyphHeight {
        mChildren.front()->getTextCache() == nullptr ?
            0.0f :
            static_cast<float>(mChildren.front()->getTextCache()->metrics.maxGlyphHeight)};

    if (cacheGlyphHeight > 0.0f)
        combinedHeight = cacheGlyphHeight * lineSpacing;
    else
        return;

    // Calculate the spacing which will be used to clip the container.
    if (lineSpacing > 1.2f && mClipSpacing == 0.0f) {
        const float minimumSpacing {mChildren.front()->getFont()->getHeight(1.2f)};
        const float currentSpacing {mChildren.front()->getFont()->getHeight(lineSpacing)};
        mClipSpacing = std::round((currentSpacing - minimumSpacing) / 2.0f);
    }

    // Resize container to font height boundary to avoid rendering a fraction of the last line.
    if (!mUpdatedSize) {
        if (mVerticalSnap) {
            float numLines {std::floor(mSize.y / combinedHeight)};
            if (numLines == 0)
                numLines = 1;
            mAdjustedHeight = std::ceil(numLines * combinedHeight);
        }
        else {
            mAdjustedHeight = mSize.y;
        }
        mUpdatedSize = true;
    }

    // deck-patches TOUCH: a finger-parked position stays put - no auto advance, no
    // end-fade reset yanking it back to the top, and no reset from the media-viewer /
    // menu-open branch below (a menu round-trip or viewer visit must not lose the
    // parked spot). Only resetComponent() (next game) clears it.
    if (mManualScrolled) {
        GuiComponent::update(deltaTime);
        return;
    }

    // Don't scroll if the media viewer or screensaver is active or if text scrolling is disabled;
    if (mWindow->isMediaViewerActive() || mWindow->isScreensaverActive() ||
        !mWindow->getAllowTextScrolling()) {
        mAutoScrollAccumulator = -mAutoScrollDelay;
        if (mScrollPos != glm::vec2 {0.0f, 0.0f} && !mWindow->isLaunchScreenDisplayed())
            resetComponent();
        return;
    }

    if (!mAdjustedAutoScrollSpeed) {
        float fontSize {static_cast<float>(mChildren.front()->getFont()->getSize())};
        float width {contentSize.x / (fontSize * 1.3f)};

        // Keep speed adjustments within reason.
        float speedModifier {glm::clamp(width, 10.0f, 40.0f)};

        speedModifier *= mAutoScrollSpeedConstant;
        speedModifier /= mResolutionModifier;
        mAdjustedAutoScrollSpeed = static_cast<int>(speedModifier);
    }

    // If there are less than 8 lines of text, accelerate the scrolling further.
    const float lines {mAdjustedHeight / combinedHeight};
    if (lines < 8.0f)
        rowModifier = lines / 8.0f;

    if (mAdjustedAutoScrollSpeed < 0)
        mAdjustedAutoScrollSpeed = 1;

    if (mAdjustedAutoScrollSpeed != 0) {
        mAutoScrollAccumulator += deltaTime;
        while (mAutoScrollAccumulator >=
               static_cast<int>(rowModifier * static_cast<float>(mAdjustedAutoScrollSpeed))) {
            if (!mAtEnd && contentSize.y > mAdjustedHeight)
                mScrollPos += mScrollDir;
            mAutoScrollAccumulator -=
                static_cast<int>(rowModifier * static_cast<float>(mAdjustedAutoScrollSpeed));
        }
    }

    // Clip scrolling within bounds.
    if (mScrollPos.x < 0.0f)
        mScrollPos.x = 0.0f;
    if (mScrollPos.y < 0.0f)
        mScrollPos.y = 0.0f;

    if (mScrollPos.x + std::round(mSize.x) > contentSize.x) {
        mScrollPos.x = contentSize.x - std::round(mSize.x);
        mAtEnd = true;
    }

    if (contentSize.y < mAdjustedHeight)
        mScrollPos.y = 0.0f;
    else if (mScrollPos.y + mAdjustedHeight > contentSize.y)
        mAtEnd = true;

    if (mAtEnd) {
        mAutoScrollResetAccumulator += deltaTime;
        if (mAutoScrollResetAccumulator >= static_cast<int>(mAutoScrollResetDelayConstant)) {
            // Fade in the text as it resets to the start position.
            float maxOpacity {static_cast<float>(mChildren.front()->getColor() & 0x000000FF) /
                              255.0f};
            auto func = [this, maxOpacity](float t) {
                unsigned int color {mChildren.front()->getColor()};
                float opacity {glm::mix(0.0f, maxOpacity, t)};
                color = (color & 0xFFFFFF00) + static_cast<unsigned char>(opacity * 255.0f);
                this->mChildren.front()->setColor(color);
                mScrollPos = glm::vec2 {};
                mAutoScrollResetAccumulator = 0;
                mAutoScrollAccumulator = -mAutoScrollDelay + mAutoScrollSpeed;
                mAtEnd = false;
            };
            this->setAnimation(new LambdaAnimation(func, 300), 0, nullptr, false);
        }
    }

    GuiComponent::update(deltaTime);
}

// deck-patches TOUCH: vertical drag scrolls the description 1:1; the whole rect is a
// dead zone otherwise, because anything falling through here either scrolls the
// gamelist behind the text or (in the scraper dialog) backs the dialog out.
bool ScrollableContainer::pointerInput(const PointerEvent& event, const glm::mat4& parentTrans)
{
    if (!isVisible() || mChildren.empty() || mChildren.front()->getValue() == "")
        return false; // mirror render(): an empty description is not a surface
    const glm::mat4 trans {parentTrans * getTransform()};
    if (!pointerWithinBounds(event, trans))
        return false;
    if (event.type != PointerEvent::Type::SCROLL)
        return true; // TAP: consume, no action

    // A finger drag moves the text WITH it, so a downward drag scrolls up: negate.
    scrollBy(-event.delta.y);
    return true;
}

bool ScrollableContainer::scrollBy(float delta)
{
    if (mChildren.empty() || mChildren.front()->getValue() == "")
        return false;
    // The end-fade animation writes mScrollPos every tick; run it to completion (full
    // opacity, top) before the manual scroll owns the position - cancel would strand the
    // text half-faded since the same lambda restores the color.
    if (isAnimationPlaying(0))
        finishAnimation(0);

    // Not laid out yet (first update pending): consume without moving.
    const float viewHeight {mAdjustedHeight > 0.0f ? mAdjustedHeight : mSize.y};
    if (viewHeight <= 0.0f || mChildren.front()->getTextCache() == nullptr)
        return true;
    const float maxScroll {
        std::max(0.0f, std::round(mChildren.front()->getSize().y) - viewHeight)};
    const float wanted {glm::clamp(mScrollPos.y + delta, 0.0f, maxScroll)};
    if (wanted == mScrollPos.y)
        return true; // clamped to where we already were: nothing moved, so claim nothing
    mScrollPos.y = wanted;
    // Set only AFTER something actually moved. A finger drag always carries a real delta,
    // but a d-pad press at the top or bottom clamps to a no-op - and setting the flag there
    // parked the auto-scroll cycle for the life of the dialog with no visible cause. The
    // popup appears, you press up during the 4.5 s pre-scroll delay while still at position
    // zero, and it simply never scrolls.
    mManualScrolled = true;
    // The manual scroll owns the end state: re-derive mAtEnd instead of letting a stale
    // flag arm the 7 s fade-reset while the user reads mid-text.
    mAtEnd = false;
    mAutoScrollResetAccumulator = 0;
    return true;
}

void ScrollableContainer::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mThemeOpacity == 0.0f || mChildren.front()->getValue() == "")
        return;

    glm::mat4 trans {parentTrans * getTransform()};

    glm::ivec2 clipPos {static_cast<int>(trans[3].x), static_cast<int>(trans[3].y)};
    glm::ivec2 clipDim {mSize.x, mAdjustedHeight};

    // By effectively clipping the upper and lower boundaries of the container we mostly avoid
    // scrolling outside the vertical starting and ending positions.
    clipPos.y += static_cast<int>(mClipSpacing);
    clipDim.y -= static_cast<int>(mClipSpacing);

    mRenderer->pushClipRect(clipPos, clipDim);

    trans = glm::translate(trans, -glm::vec3 {mScrollPos.x, mScrollPos.y, 0.0f});
    mRenderer->setMatrix(trans);

    if (Settings::getInstance()->getBool("DebugText"))
        mRenderer->drawRect(0.0f, mScrollPos.y, mSize.x, mAdjustedHeight, 0x0000FF33, 0x0000FF33);

    GuiComponent::renderChildren(trans);
    mRenderer->popClipRect();
}
