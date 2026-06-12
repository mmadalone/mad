//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadSpriteCanvas.h
//
//  Sprite test canvas for the MAD controller testers (deck-patches): a base
//  image (plus optional back view beside it) with overlay sprite items at
//  positions NORMALIZED to the core box — the exact coordinate model of the
//  Tk testers, so saved/baked gp-*-positions.json layouts carry over
//  pixel-faithfully. Items show/hide on press; stick items swap between
//  token images (lstick_up, JoystickUL, …). Edit mode exposes a selection
//  cursor the page nudges with the d-pad; positions() returns the same
//  normalized JSON the Tk testers saved.
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_SPRITE_CANVAS_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_SPRITE_CANVAS_H

#include "components/ImageComponent.h"
#include "renderers/Renderer.h"

#include <map>
#include <memory>
#include <string>
#include <vector>

class MadSpriteCanvas : public GuiComponent
{
public:
    MadSpriteCanvas();

    // Base (+ optional back) image paths; call before addItem.
    void setBase(const std::string& basePath, const std::string& backPath = "");
    // One overlay item: images = token → path ("on" for plain pressed art;
    // stick tokens like "up"/"UL"/"rest" for swappable sets).
    void addItem(const std::string& key, const float nx, const float ny,
                 const std::map<std::string, std::string>& images,
                 const bool alwaysVisible = false, const std::string& restToken = "");
    void clearItems();

    // Live state.
    void setItemVisible(const std::string& key, const bool on);
    void setItemToken(const std::string& key, const std::string& token);
    void setAllVisible(const bool on); // Preview/edit (always-visible items stay).
    void resetItems();                 // Back to resting state.

    // Edit/calibrate selection: the page cycles + nudges; the canvas draws the
    // selection outline around the current item.
    void setSelectionVisible(const bool on);
    void cycleSelection(const int direction);
    std::string selectedKey() const;
    void nudgeSelected(const float dxPixels, const float dyPixels);
    // Normalized positions (the Tk JSON format) of every item.
    std::map<std::string, std::pair<float, float>> positions() const;
    void setPositions(const std::map<std::string, std::pair<float, float>>& positions);

    void render(const glm::mat4& parentTrans) override;
    void onSizeChanged() override;

private:
    struct Item {
        std::string key;
        float nx {0.5f};
        float ny {0.5f};
        std::map<std::string, std::shared_ptr<ImageComponent>> images;
        std::string token;     // Currently shown token.
        std::string restToken; // "" = hidden at rest; else shown with this token.
        bool visible {false};
        bool alwaysVisible {false};
    };

    void layout();
    glm::vec2 itemCenter(const Item& item) const;

    Renderer* mRenderer;
    std::shared_ptr<ImageComponent> mBase;
    std::shared_ptr<ImageComponent> mBack;
    std::string mBasePath;
    std::string mBackPath;
    std::vector<Item> mItems;
    float mFactor;     // native px → screen px.
    float mCoreWidth;  // base + gap + back, NATIVE px (the normalization box).
    float mCoreHeight;
    float mGap;
    int mSelection;
    bool mSelectionVisible;
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_SPRITE_CANVAS_H
