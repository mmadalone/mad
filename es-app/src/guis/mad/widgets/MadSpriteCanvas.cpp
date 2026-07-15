//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadSpriteCanvas.cpp
//
//  Sprite test canvas for the MAD controller testers (deck-patches).
//

#include "guis/mad/widgets/MadSpriteCanvas.h"

#include <cmath>
#include "guis/mad/MadTheme.h"

MadSpriteCanvas::MadSpriteCanvas()
    : mRenderer {Renderer::getInstance()}
    , mFactor {1.0f}
    , mCoreWidth {1.0f}
    , mCoreHeight {1.0f}
    , mGap {18.0f}
    , mSelection {0}
    , mSelectionVisible {false}
{
}

void MadSpriteCanvas::setBase(const std::string& basePath, const std::string& backPath)
{
    mBasePath = basePath;
    mBackPath = backPath;
    mBase = std::make_shared<ImageComponent>();
    mBase->setImage(basePath); // No resize yet: getSize() = native texture px.
    if (!backPath.empty()) {
        mBack = std::make_shared<ImageComponent>();
        mBack->setImage(backPath);
    }
    else {
        mBack.reset();
    }
    // The Tk normalization box: base + gap + back at NATIVE texture scale.
    const float backWidth {
        mBack != nullptr ? mGap + static_cast<float>(mBack->getTextureSize().x) : 0.0f};
    mCoreWidth = std::max(1.0f, static_cast<float>(mBase->getTextureSize().x) + backWidth);
    mCoreHeight = std::max(
        1.0f, std::max(static_cast<float>(mBase->getTextureSize().y),
                       mBack != nullptr ? static_cast<float>(mBack->getTextureSize().y) :
                                          0.0f));
    layout();
}

void MadSpriteCanvas::addItem(const std::string& key, const float nx, const float ny,
                              const std::map<std::string, std::string>& images,
                              const bool alwaysVisible, const std::string& restToken)
{
    Item item;
    item.key = key;
    item.nx = nx;
    item.ny = ny;
    item.restToken = restToken;
    item.token = restToken.empty() ? (images.count("on") ? "on" : "") : restToken;
    item.alwaysVisible = alwaysVisible;
    item.visible = alwaysVisible;
    for (const auto& entry : images) {
        auto image = std::make_shared<ImageComponent>();
        image->setImage(entry.second); // Native size; scaled in layout().
        image->setOrigin(0.5f, 0.5f);
        item.images[entry.first] = image;
    }
    if (item.token.empty() && !item.images.empty())
        item.token = item.images.begin()->first;
    mItems.emplace_back(std::move(item));
    layout();
}

void MadSpriteCanvas::clearItems()
{
    mItems.clear();
    mSelection = 0;
    mSelectionVisible = false;
}

void MadSpriteCanvas::onSizeChanged()
{
    layout();
}

void MadSpriteCanvas::layout()
{
    if (mBase == nullptr || mSize.x <= 0.0f || mSize.y <= 0.0f)
        return;
    // Fit the native core box into the canvas — upscaling included, so the art
    // fills whatever box the page grants (positions stay normalized).
    mFactor = std::min(mSize.x / mCoreWidth, mSize.y / mCoreHeight);
    mBase->setResize(mBase->getTextureSize().x * mFactor, 0.0f);
    mBase->setPosition(0.0f, 0.0f);
    mBase->setOrigin(0.0f, 0.0f);
    if (mBack != nullptr) {
        mBack->setResize(mBack->getTextureSize().x * mFactor, 0.0f);
        mBack->setOrigin(0.0f, 0.0f);
        mBack->setPosition(mBase->getSize().x + mGap * mFactor, 0.0f);
    }
    for (Item& item : mItems) {
        const glm::vec2 center {itemCenter(item)};
        for (auto& entry : item.images) {
            entry.second->setResize(entry.second->getTextureSize().x * mFactor, 0.0f);
            entry.second->setPosition(center.x, center.y);
        }
    }
}

glm::vec2 MadSpriteCanvas::itemCenter(const Item& item) const
{
    return glm::vec2 {item.nx * mCoreWidth * mFactor, item.ny * mCoreHeight * mFactor};
}

void MadSpriteCanvas::setItemVisible(const std::string& key, const bool on)
{
    for (Item& item : mItems) {
        if (item.key == key)
            item.visible = on || item.alwaysVisible;
    }
}

void MadSpriteCanvas::setItemToken(const std::string& key, const std::string& token)
{
    for (Item& item : mItems) {
        if (item.key == key && item.images.count(token))
            item.token = token;
    }
}

void MadSpriteCanvas::setAllVisible(const bool on)
{
    for (Item& item : mItems)
        item.visible = on || item.alwaysVisible;
}

void MadSpriteCanvas::resetItems()
{
    for (Item& item : mItems) {
        item.visible = item.alwaysVisible;
        if (!item.restToken.empty())
            item.token = item.restToken;
    }
}

void MadSpriteCanvas::setSelectionVisible(const bool on)
{
    mSelectionVisible = on;
}

void MadSpriteCanvas::cycleSelection(const int direction)
{
    if (mItems.empty())
        return;
    mSelection = (mSelection + direction + static_cast<int>(mItems.size())) %
                 static_cast<int>(mItems.size());
}

std::string MadSpriteCanvas::selectedKey() const
{
    if (mItems.empty() || mSelection < 0 || mSelection >= static_cast<int>(mItems.size()))
        return "";
    return mItems[mSelection].key;
}

void MadSpriteCanvas::nudgeSelected(const float dxPixels, const float dyPixels)
{
    if (mItems.empty() || mSelection < 0 || mSelection >= static_cast<int>(mItems.size()))
        return;
    Item& item {mItems[mSelection]};
    const float scaleX {mCoreWidth * mFactor};
    const float scaleY {mCoreHeight * mFactor};
    if (scaleX <= 0.0f || scaleY <= 0.0f)
        return;
    item.nx = glm::clamp(item.nx + dxPixels / scaleX, 0.0f, 1.0f);
    item.ny = glm::clamp(item.ny + dyPixels / scaleY, 0.0f, 1.0f);
    const glm::vec2 center {itemCenter(item)};
    for (auto& entry : item.images)
        entry.second->setPosition(center.x, center.y);
}

void MadSpriteCanvas::setCursorVisible(const bool on)
{
    mCursorVisible = on;
}

void MadSpriteCanvas::centerCursor()
{
    mCursorNx = 0.5f;
    mCursorNy = 0.5f;
}

void MadSpriteCanvas::moveCursor(const float dxPixels, const float dyPixels)
{
    const float scaleX {mCoreWidth * mFactor};
    const float scaleY {mCoreHeight * mFactor};
    if (scaleX <= 0.0f || scaleY <= 0.0f)
        return;
    mCursorNx = glm::clamp(mCursorNx + dxPixels / scaleX, 0.0f, 1.0f);
    mCursorNy = glm::clamp(mCursorNy + dyPixels / scaleY, 0.0f, 1.0f);
    if (mGrabbed)
        dragSelectedToCursor();
}

int MadSpriteCanvas::hitTest(const float px, const float py) const
{
    int best {-1};
    float bestDist {0.0f};
    for (size_t i {0}; i < mItems.size(); ++i) {
        const glm::vec2 center {itemCenter(mItems[i])};
        const float dist {glm::length(glm::vec2 {px, py} - center)};
        // Hit radius: the shown image's half-extent, floored so tiny sprites stay grabbable.
        const auto it {mItems[i].images.find(mItems[i].token)};
        const glm::vec2 half {it != mItems[i].images.end() ? it->second->getSize() / 2.0f :
                                                             glm::vec2 {12.0f, 12.0f}};
        const float radius {std::max(14.0f * mFactor, std::max(half.x, half.y))};
        if (dist <= radius && (best < 0 || dist < bestDist)) {
            best = static_cast<int>(i);
            bestDist = dist;
        }
    }
    return best;
}

void MadSpriteCanvas::dragSelectedToCursor()
{
    if (mItems.empty() || mSelection < 0 || mSelection >= static_cast<int>(mItems.size()))
        return;
    Item& item {mItems[mSelection]};
    item.nx = mCursorNx;
    item.ny = mCursorNy;
    const glm::vec2 center {itemCenter(item)};
    for (auto& entry : item.images)
        entry.second->setPosition(center.x, center.y);
}

bool MadSpriteCanvas::grabAtCursor()
{
    const int idx {hitTest(mCursorNx * mCoreWidth * mFactor, mCursorNy * mCoreHeight * mFactor)};
    if (idx < 0)
        return false;
    mSelection = idx;
    mGrabbed = true;
    dragSelectedToCursor(); // snap the grabbed sprite to the cursor
    return true;
}

void MadSpriteCanvas::releaseGrab()
{
    mGrabbed = false;
}

std::map<std::string, std::pair<float, float>> MadSpriteCanvas::positions() const
{
    std::map<std::string, std::pair<float, float>> out;
    for (const Item& item : mItems)
        out[item.key] = {item.nx, item.ny};
    return out;
}

void MadSpriteCanvas::setPositions(
    const std::map<std::string, std::pair<float, float>>& positions)
{
    for (Item& item : mItems) {
        const auto it = positions.find(item.key);
        if (it != positions.end()) {
            item.nx = it->second.first;
            item.ny = it->second.second;
        }
    }
    layout();
}

void MadSpriteCanvas::render(const glm::mat4& parentTrans)
{
    if (!isVisible() || mBase == nullptr)
        return;
    glm::mat4 trans {parentTrans * getTransform()};
    mBase->render(trans);
    if (mBack != nullptr)
        mBack->render(trans);
    for (Item& item : mItems) {
        if (!item.visible)
            continue;
        const auto it = item.images.find(item.token);
        if (it != item.images.end())
            it->second->render(trans);
    }
    // Outline the GRABBED sprite, or (pre-grab, in edit) the one the cursor hovers — so the
    // user sees what a click will grab; in calibrate the cycled selection. Never an arbitrary #0.
    int outlineIdx {-1};
    if (mCursorVisible)
        outlineIdx = mGrabbed ? mSelection
                              : hitTest(mCursorNx * mCoreWidth * mFactor,
                                        mCursorNy * mCoreHeight * mFactor);
    else if (mSelectionVisible && !mItems.empty())
        outlineIdx = mSelection;
    if (outlineIdx >= 0 && outlineIdx < static_cast<int>(mItems.size())) {
        const Item& item {mItems[outlineIdx]};
        const auto it = item.images.find(item.token.empty() ? "on" : item.token);
        const glm::vec2 center {itemCenter(item)};
        const glm::vec2 half {it != item.images.end() ?
                                  it->second->getSize() / 2.0f :
                                  glm::vec2 {12.0f, 12.0f}};
        const float stroke {std::max(2.0f, 2.5f * Renderer::getScreenHeightModifier())};
        mRenderer->setMatrix(trans);
        mRenderer->drawRect(center.x - half.x, center.y - half.y, half.x * 2.0f, stroke,
                            MadTheme::color(MadColor::Green), MadTheme::color(MadColor::Green));
        mRenderer->drawRect(center.x - half.x, center.y + half.y - stroke, half.x * 2.0f,
                            stroke, MadTheme::color(MadColor::Green), MadTheme::color(MadColor::Green));
        mRenderer->drawRect(center.x - half.x, center.y - half.y, stroke, half.y * 2.0f,
                            MadTheme::color(MadColor::Green), MadTheme::color(MadColor::Green));
        mRenderer->drawRect(center.x + half.x - stroke, center.y - half.y, stroke,
                            half.y * 2.0f, MadTheme::color(MadColor::Green), MadTheme::color(MadColor::Green));
    }
    if (mCursorVisible) {
        // Trackball cursor — a crosshair, tinted distinctly from the green grab outline.
        const glm::vec2 c {mCursorNx * mCoreWidth * mFactor, mCursorNy * mCoreHeight * mFactor};
        const float len {std::max(10.0f, 12.0f * Renderer::getScreenHeightModifier())};
        const float stroke {std::max(2.0f, 2.5f * Renderer::getScreenHeightModifier())};
        const unsigned int col {MadTheme::color(MadColor::Selector)};
        mRenderer->setMatrix(trans);
        mRenderer->drawRect(c.x - len, c.y - stroke * 0.5f, len * 2.0f, stroke, col, col);
        mRenderer->drawRect(c.x - stroke * 0.5f, c.y - len, stroke, len * 2.0f, col, col);
    }
}
