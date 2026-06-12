//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadPage.cpp
//
//  Abstract base class for MAD control panel pages (deck-patches).
//

#include "guis/mad/MadPage.h"

#include "guis/mad/GuiMadPanel.h"

MadPage::MadPage(GuiMadPanel* panel, const std::string& title)
    : mPanel {panel}
    , mViewportPos {0.0f, 0.0f}
    , mViewportSize {0.0f, 0.0f}
    , mFocusCookie {0}
    , mAliveToken {std::make_shared<int>(0)}
{
    // Medium (~half the large font): the large title ate too much vertical
    // space on a TV — the viewport below reclaims the difference.
    mTitle = std::make_shared<TextComponent>(title, Font::get(FONT_SIZE_MEDIUM), mMenuColorTitle,
                                             ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    addChild(mTitle.get());
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
    backend()->request(
        method, params,
        [alive, callback](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
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
            text, Font::get(FONT_SIZE_MEDIUM), mMenuColorSecondary, ALIGN_CENTER, ALIGN_CENTER,
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
