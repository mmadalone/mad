//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadSidebar.h
//
//  Section sidebar for the MAD control panel (deck-patches). Purely display:
//  the active section is switched with the shoulder buttons, not focused here.
//

#ifndef ES_APP_GUIS_MAD_MAD_SIDEBAR_H
#define ES_APP_GUIS_MAD_MAD_SIDEBAR_H

#include "components/ImageComponent.h"
#include "components/TextComponent.h"
#include "renderers/Renderer.h"

#include <memory>
#include <string>
#include <vector>

class MadSidebar : public GuiComponent
{
public:
    MadSidebar(const std::vector<std::string>& labels);

    void setActive(const int index);
    void setIcon(const int index, const std::string& path);

    void onSizeChanged() override;
    void render(const glm::mat4& parentTrans) override;

private:
    struct Entry {
        std::shared_ptr<ImageComponent> icon;
        std::shared_ptr<TextComponent> label;
    };

    Renderer* mRenderer;
    std::vector<Entry> mEntries;
    int mActive;
    float mEntryHeight;
    float mIconSize;
};

#endif // ES_APP_GUIS_MAD_MAD_SIDEBAR_H
