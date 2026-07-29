//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadPage.cpp
//
//  Abstract base class for MAD control panel pages (deck-patches).
//

#include "guis/mad/MadPage.h"

#include "Log.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/widgets/MadReorderList.h" // deck-patches TOUCH
#include "guis/mad/widgets/MadScrollView.h" // deck-patches TOUCH

#include <chrono>

MadPage::MadPage(GuiMadPanel* panel, const std::string& title)
    : mPanel {panel}
    , mViewportPos {0.0f, 0.0f}
    , mViewportSize {0.0f, 0.0f}
    , mFocusCookie {0}
    , mAliveToken {std::make_shared<int>(0)}
{
    // Medium (~half the large font): the large title ate too much vertical
    // space on a TV — the viewport below reclaims the difference.
    mTitle = std::make_shared<TextComponent>(title, Font::get(FONT_SIZE_MEDIUM), MadTheme::color(MadColor::Title),
                                             ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    addChild(mTitle.get());
}

// deck-patches TOUCH: see MadPage.h. The scroll view registration also arms the
// view's own inside-bounds enforcement (setCarryList), so one call covers both.
void MadPage::setTouchCarry(const std::shared_ptr<MadScrollView>& scrollView,
                            const std::shared_ptr<MadReorderList>& list)
{
    mTouchCarryScroll = scrollView;
    mTouchCarryList = list;
    if (scrollView != nullptr)
        scrollView->setCarryList(list);
}

// deck-patches TOUCH: carry-owns-the-page dispatch, see MadPage.h.
bool MadPage::pointerInput(const PointerEvent& event, const glm::mat4& parentTrans)
{
    const std::shared_ptr<MadReorderList> carryList {mTouchCarryList.lock()};
    if (carryList != nullptr && carryList->carrying()) {
        const std::shared_ptr<MadScrollView> carryScroll {mTouchCarryScroll.lock()};
        if (carryScroll != nullptr &&
            carryScroll->pointerInput(event, parentTrans * getTransform()))
            return true;
        // A tap anywhere else on the page drops the carried row in place first;
        // the user's next tap then acts on the committed order.
        if (event.type == PointerEvent::Type::TAP)
            carryList->dropCarry(); // LAST action against this branch.
        return true;
    }

    return GuiComponent::pointerInput(event, parentTrans);
}

void MadPage::onSizeChanged()
{
    const float titleHeight {Font::get(FONT_SIZE_MEDIUM)->getHeight() * 1.1f};
    const float spacing {Font::get(FONT_SIZE_MEDIUM)->getHeight() * 0.3f};
    const float reserved {mTitleHidden ? 0.0f : titleHeight + spacing};

    mTitle->setVisible(!mTitleHidden);
    mTitle->setPosition(0.0f, 0.0f);
    mTitle->setSize(mSize.x, titleHeight);

    mViewportPos = {0.0f, reserved};
    mViewportSize = {mSize.x, mSize.y - reserved};
}

void MadPage::setTitleHidden(const bool hidden)
{
    mTitleHidden = hidden;
    if (mSize.x > 0.0f)
        onSizeChanged();
}

int MadPage::pickPagedTarget(const std::vector<PagedTarget>& targets,
                             const int direction,
                             const float viewTop,
                             const float viewBottom)
{
    // Targets come pre-sorted by top (pages build them in layout order).
    int pick {-1};
    for (size_t i {0}; i < targets.size(); ++i) {
        if (targets[i].top < viewTop || targets[i].top > viewBottom)
            continue;
        if (direction > 0)
            pick = static_cast<int>(i); // Lowest qualifying wins on page-down.
        else if (pick == -1)
            pick = static_cast<int>(i); // Highest qualifying wins on page-up.
    }
    return pick;
}

void MadPage::pageRequest(const std::string& method,
                          const MadJson::ParamsWriter& params,
                          const MadBackend::ResponseCallback& callback,
                          const int timeoutMs)
{
    std::weak_ptr<int> alive {mAliveToken};
    // Page-load instrumentation: record how long each backend request took to
    // feed this page. Auto-gated by ES-DE debug mode (--debug / DebugMode) since
    // LOG(LogDebug) is a no-op otherwise. With the backend's revision cache a
    // re-shown page's request returns in ~0 ms (cache hit); a genuine (re)load
    // shows the real cost — which is exactly the "page loading times" signal.
    const auto started {std::chrono::steady_clock::now()};
    const std::string title {mTitle ? mTitle->getValue() : std::string {}};
    backend()->request(
        method, params,
        [alive, callback, method, title, started](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            const auto ms {std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::steady_clock::now() - started)
                               .count()};
            LOG(LogDebug) << "MadPage[" << (title.empty() ? "?" : title) << "]: " << method
                          << (ok ? " loaded in " : " FAILED after ") << ms << " ms";
            if (callback)
                callback(ok, payload);
        },
        timeoutMs);
}

void MadPage::setLoadingText(const std::string& text)
{
    if (text.empty()) {
        if (mLoadingText != nullptr) {
            removeChild(mLoadingText.get());
            mLoadingText.reset();
        }
        return;
    }

    if (mLoadingText == nullptr) {
        mLoadingText = std::make_shared<TextComponent>(
            text, Font::get(FONT_SIZE_MEDIUM), MadTheme::color(MadColor::Secondary), ALIGN_CENTER, ALIGN_CENTER,
            glm::ivec2 {0, 0});
        mLoadingText->setPosition(mViewportPos.x, mViewportPos.y);
        mLoadingText->setSize(mViewportSize);
        addChild(mLoadingText.get());
    }
    else {
        mLoadingText->setText(text);
    }
}

MadBackend* MadPage::backend() const
{
    return mPanel->getBackend();
}

MadFooter* MadPage::footer() const
{
    return mPanel->getFooter();
}
