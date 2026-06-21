//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSidebar.cpp
//
//  MAD control panel: "Sidebar" section (deck-patches).
//

#include "utils/LocalizationUtil.h" // _() macro — must precede OptionListComponent.h (its template uses it)

#include "guis/mad/pages/GuiMadPageSidebar.h"

// OptionListComponent.h is not self-contained: its template body references MenuComponent
// and the _() macro, so both must be visible before it is parsed.
#include "components/MenuComponent.h"
#include "components/OptionListComponent.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

GuiMadPageSidebar::GuiMadPageSidebar(GuiMadPanel* panel)
    : MadPage {panel, "SIDEBAR"}
{
}

void GuiMadPageSidebar::build()
{
    setLoadingText("Loading sidebar options…");
    requestSections();
}

void GuiMadPageSidebar::requestSections()
{
    pageRequest(
        "sidebar.sections", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load sidebar options: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            populate(payload);
        });
}

void GuiMadPageSidebar::populate(const rapidjson::Value& result)
{
    const rapidjson::Value& sections {MadJson::getMember(result, "sections")};
    if (!sections.IsArray())
        return;

    float contentY {mViewportPos.y};
    mIntro = std::make_shared<TextComponent>(
        "Hardware rows auto-hide until you can use them. Override per row:",
        Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary), ALIGN_CENTER, ALIGN_CENTER,
        glm::ivec2 {0, 0});
    mIntro->setPosition(mViewportPos.x, contentY);
    mIntro->setSize(mViewportSize.x, Font::get(FONT_SIZE_SMALL)->getHeight());
    addChild(mIntro.get());
    contentY += mIntro->getSize().y + mViewportSize.y * 0.03f;

    mList = std::make_shared<ComponentList>();
    mList->setPosition(mViewportPos.x, contentY);
    mList->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - contentY);
    addChild(mList.get());

    bool any {false};
    for (rapidjson::SizeType i {0}; i < sections.Size(); ++i) {
        const rapidjson::Value& s {sections[i]};
        if (MadJson::getBool(s, "core", false))
            continue;   // core rows are always shown — nothing to toggle
        any = true;
        const std::string key {MadJson::getString(s, "key")};
        const std::string label {MadJson::getString(s, "label", key)};
        const bool fshow {MadJson::getBool(s, "force_show", false)};
        const bool fhide {MadJson::getBool(s, "force_hide", false)};
        const bool cap {MadJson::getBool(s, "capability_met", false)};
        const std::string mode {fshow ? "show" : (fhide ? "hide" : "auto")};
        const std::string sub {cap ? "  (auto: shown)" : "  (auto: hidden)"};

        auto opt = std::make_shared<OptionListComponent<std::string>>("sidebar-mode");
        opt->add("Auto", "auto", mode == "auto");
        opt->add("Always show", "show", mode == "show");
        opt->add("Always hide", "hide", mode == "hide");
        opt->setCallback([this, key](const std::string& value) { setMode(key, value); });

        ComponentListRow row;
        row.addElement(std::make_shared<TextComponent>(label + sub, Font::get(FONT_SIZE_MEDIUM),
                                                       MadTheme::color(MadColor::Primary), ALIGN_LEFT,
                                                       ALIGN_CENTER, glm::ivec2 {0, 0}),
                       true);
        row.addElement(opt, false);
        mList->addRow(row);
    }

    if (!any)
        setLoadingText("All sidebar rows are core (always shown).");
    else
        mList->onFocusGained();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageSidebar::setMode(const std::string& key, const std::string& mode)
{
    pageRequest(
        "sidebar.set",
        [key, mode](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("mode");
            writer.String(mode.c_str(), static_cast<rapidjson::SizeType>(mode.length()));
        },
        [this, key, mode](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->setStatus("Couldn't set " + key + ": " +
                                        MadJson::getString(payload, "message", "error"),
                                    true);
                return;
            }
            footer()->setStatus(key + " → " + mode + " (applies next time you open the panel)", false);
        });
}
