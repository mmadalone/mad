//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadPageUtil.h
//
//  MAD control panel: small shared helpers for the page classes (deck-patches). One source of
//  truth for scaffolding that had drifted into copy-paste across sibling pages:
//    - lower():          case-fold a string for filter matching (was duplicated verbatim in the
//                        bezel pages' anonymous namespaces).
//    - uniquifyPadLabels() / labelsToIds(): the pad reorder-list label<->id mapping shared by
//                        GuiMadPagePergamePads and GuiMadPagePadsPriority (their dedup + apply
//                        loops were byte-identical).
//    - makeBezelPreview(): the 60/40 list + preview pane's ImageComponent, identical across the
//                        three bezel pages.
//  Header-only inline (mirrors MadJson.h / MadTheme.h) so there is a single definition and no new
//  translation unit. Behavior is byte-for-byte what the pages did inline.
//

#ifndef ES_APP_GUIS_MAD_MAD_PAGE_UTIL_H
#define ES_APP_GUIS_MAD_MAD_PAGE_UTIL_H

#include "components/ImageComponent.h"
#include "guis/mad/MadJson.h"

#include <algorithm>
#include <cctype>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace MadPageUtil
{
    // Case-fold for filter matching (ASCII tolower via unsigned char, as UB-safe as std::tolower).
    inline std::string lower(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(),
                       [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        return s;
    }

    // Build the reorder list's display labels + a label->pad-id map from a "pads" JSON array,
    // keeping labels unique even when two pads share a name. Shared by the two pad reorder pages
    // (GuiMadPagePergamePads, GuiMadPagePadsPriority) at rebuild time.
    inline void uniquifyPadLabels(const rapidjson::Value& pads,
                                  std::map<std::string, std::string>& idByLabel,
                                  std::vector<std::string>& order)
    {
        if (!pads.IsArray())
            return;
        for (rapidjson::SizeType i {0}; i < pads.Size(); ++i) {
            const std::string id {MadJson::getString(pads[i], "id")};
            std::string label {MadJson::getString(pads[i], "label")};
            if (label.empty())
                label = id;
            // Keep the reorder-list keys unique even if two pads share a name.
            std::string uniq {label};
            int n {2};
            while (idByLabel.count(uniq))
                uniq = label + " (" + std::to_string(n++) + ")";
            idByLabel[uniq] = id;
            order.emplace_back(uniq);
        }
    }

    // Map an ordered list of display labels back to pad ids (dropping any that no longer resolve),
    // preserving order. Shared by the two pad reorder pages' Apply.
    inline std::vector<std::string> labelsToIds(const std::vector<std::string>& labels,
                                                const std::map<std::string, std::string>& idByLabel)
    {
        std::vector<std::string> ids;
        ids.reserve(labels.size());
        for (const std::string& label : labels) {
            const auto it {idByLabel.find(label)};
            if (it != idByLabel.end())
                ids.push_back(it->second);
        }
        return ids;
    }

    // The bezel pages' right-hand preview pane: a top-centered ImageComponent sized to the pane
    // reserved to the right of a listWidth-wide list. The caller still addChild()s the result.
    inline std::shared_ptr<ImageComponent> makeBezelPreview(const glm::vec2& viewportPos,
                                                            const glm::vec2& viewportSize,
                                                            const float listWidth)
    {
        const float paneLeft {viewportPos.x + listWidth};
        const float paneWidth {viewportSize.x - listWidth};
        auto preview {std::make_shared<ImageComponent>()};
        preview->setOrigin(0.5f, 0.0f);
        preview->setMaxSize(paneWidth * 0.9f, viewportSize.y * 0.6f);
        preview->setPosition(paneLeft + paneWidth * 0.5f, viewportPos.y);
        return preview;
    }
} // namespace MadPageUtil

#endif // ES_APP_GUIS_MAD_MAD_PAGE_UTIL_H
