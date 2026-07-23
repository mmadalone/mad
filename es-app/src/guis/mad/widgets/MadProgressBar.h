//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadProgressBar.h
//
//  A horizontal progress bar for the MAD control panel (deck-patches): a rounded
//  dimmed track with a green fill proportional to a 0..1 fraction, and a label
//  drawn left-aligned over the bar. Used by the cloud Transfer-progress subpage
//  (one overall bar plus one bar per active rclone transfer).
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_PROGRESS_BAR_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_PROGRESS_BAR_H

#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <memory>
#include <string>

class MadProgressBar : public GuiComponent
{
public:
    MadProgressBar();

    void setFraction(const float fraction); // 0..1, clamped
    void setLabel(const std::string& label);

    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;

private:
    Renderer* mRenderer;
    float mFraction;
    std::shared_ptr<TextComponent> mLabel;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_PROGRESS_BAR_H
