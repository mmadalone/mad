//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadFooter.h
//
//  Dynamic status line for the MAD control panel, living IN ES-DE's help row
//  (deck-patches): while it has text the PANEL SUPPRESSES the help prompts
//  (hasText/setOnVisibilityChanged → updateHelpPrompts), so statuses render
//  on the exact backdrop, font and color the prompts use — one fully dynamic
//  bottom row: prompts when idle, statuses/press readouts otherwise.
//

#ifndef ES_APP_GUIS_MAD_MAD_FOOTER_H
#define ES_APP_GUIS_MAD_MAD_FOOTER_H

#include "components/TextComponent.h"

#include <functional>
#include <memory>
#include <string>

class MadFooter : public GuiComponent
{
public:
    MadFooter();

    // Sticky status: shown until replaced (flashes overlay it temporarily).
    void setStatus(const std::string& text, const bool error = false);
    // Timed message: restores the sticky status when the duration runs out.
    void flash(const std::string& text, const int durationMs = 2500, const bool error = false);
    // Hard clear: drops the sticky AND any active flash. The panel uses it on
    // state transitions (error/connecting) where the prompts must own the
    // strip immediately — plain setStatus("") lets a flash finish first.
    void clear();

    // The panel suppresses the help prompts while the footer has text (the
    // strip is shared); this fires on every empty <-> non-empty transition.
    bool hasText() const { return !mShownText.empty(); }
    void setOnVisibilityChanged(const std::function<void()>& callback)
    {
        mOnVisibilityChanged = callback;
    }

    void update(int deltaTime) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;

private:
    void apply(const std::string& text, const bool error);

    std::function<void()> mOnVisibilityChanged;

    std::shared_ptr<TextComponent> mText;
    std::string mShownText; // What's on screen right now (sticky or flash).
    std::string mStickyText;
    bool mStickyError;
    int mFlashTimeLeft;
};

#endif // ES_APP_GUIS_MAD_MAD_FOOTER_H
