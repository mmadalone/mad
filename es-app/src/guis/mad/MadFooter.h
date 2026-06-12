//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadFooter.h
//
//  Dynamic status line for the MAD control panel, living IN ES-DE's help row
//  (deck-patches): while it has text it paints over the help prompts (Window
//  renders them before the top GUI), so the bottom strip is one fully dynamic
//  footer — prompts when idle, statuses/press readouts when there's something
//  to say.
//

#ifndef ES_APP_GUIS_MAD_MAD_FOOTER_H
#define ES_APP_GUIS_MAD_MAD_FOOTER_H

#include "components/TextComponent.h"

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

    void update(int deltaTime) override;
    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;

private:
    void apply(const std::string& text, const bool error);

    std::shared_ptr<TextComponent> mText;
    std::string mShownText; // What's on screen right now (sticky or flash).
    std::string mStickyText;
    bool mStickyError;
    int mFlashTimeLeft;
};

#endif // ES_APP_GUIS_MAD_MAD_FOOTER_H
