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
#include <cstdio>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace MadPageUtil
{
    // Human-readable byte size ("3.2 GB"). Was duplicated verbatim in the asset-list and
    // media-kinds pages' anonymous namespaces; the upload-confirm dialog made it a third copy,
    // so it moved here instead (this header's whole charter).
    inline std::string humanSize(long long bytes)
    {
        if (bytes < 1024)
            return std::to_string(bytes) + " B";
        const char* unit[] {"KB", "MB", "GB", "TB"};
        double v {static_cast<double>(bytes)};
        int i {-1};
        while (v >= 1024.0 && i < 3) {
            v /= 1024.0;
            ++i;
        }
        char buf[32];
        std::snprintf(buf, sizeof buf, "%.1f %s", v, unit[i]);
        return buf;
    }

    // The SAME size in the compact form ("3.2G", "512K", "9B"): no space, one unit letter, and no
    // decimal below a megabyte. For chips, tally lines and dense list rows, where the long form's
    // three extra glyphs cost real width — a documented choice, not the drift it used to be.
    inline std::string humanSizeCompact(long long bytes)
    {
        double n {static_cast<double>(bytes)};
        for (const char* unit : {"B", "K", "M", "G", "T"}) {
            if (n < 1024.0 || unit[0] == 'T') {
                char buf[32];
                if (unit[0] == 'B' || unit[0] == 'K')
                    std::snprintf(buf, sizeof(buf), "%.0f%s", n, unit);
                else
                    std::snprintf(buf, sizeof(buf), "%.1f%s", n, unit);
                return buf;
            }
            n /= 1024.0;
        }
        return "";
    }

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
