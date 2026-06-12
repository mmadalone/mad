//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadTheme.cpp
//
//  MAD panel theming (deck-patches).
//

#include "guis/mad/MadTheme.h"

#include "Log.h"
#include "Settings.h"
#include "ThemeData.h"
#include "utils/FileSystemUtil.h"
#include "utils/StringUtil.h"

#include <pugixml.hpp>

namespace
{
    const std::map<std::string, MadColor> COLOR_KEYS {
        {"frame", MadColor::Frame},
        {"primary", MadColor::Primary},
        {"secondary", MadColor::Secondary},
        {"title", MadColor::Title},
        {"selector", MadColor::Selector},
        {"red", MadColor::Red},
        {"green", MadColor::Green},
        {"separators", MadColor::Separators},
        {"panelDimmed", MadColor::PanelDimmed},
        {"buttonFlat", MadColor::ButtonFlatUnfocused},
        {"helpText", MadColor::HelpText},
    };

    // Stock dark-scheme constants — the LAST fallback, used only if color()
    // runs before the panel has injected the runtime defaults.
    const std::map<MadColor, unsigned int> STOCK {
        {MadColor::Frame, 0x191919FF},        {MadColor::Primary, 0x777777FF},
        {MadColor::Secondary, 0x575757FF},    {MadColor::Title, 0x999999FF},
        {MadColor::Selector, 0xFFFFFFFF},     {MadColor::Red, 0x992222FF},
        {MadColor::Green, 0x449944FF},        {MadColor::Separators, 0x303030FF},
        {MadColor::PanelDimmed, 0x00000024},  {MadColor::ButtonFlatUnfocused, 0x282828FF},
        {MadColor::HelpText, 0x777777FF},
    };

    // The 12 page names = the panel's artKeys.
    const std::vector<std::string> PAGES {
        "preview", "systems",  "priority", "players",  "quit-combo", "backends",
        "lightgun", "daphne",  "x-arcade", "gamepads", "splash",     "backup"};
} // namespace

MadTheme& MadTheme::getInstance()
{
    static MadTheme instance;
    return instance;
}

void MadTheme::load(const std::map<MadColor, unsigned int>& defaults)
{
    mDefaults = defaults;
    mColors.clear();
    mIcons.clear();
    mVariables.clear();

    const auto& themes {ThemeData::getThemes()};
    const auto it = themes.find(Settings::getInstance()->getString("Theme"));
    if (it == themes.cend())
        return;
    const std::string base {it->second.path + "/router-config/"};
    parseFile(base + "mad-theme.xml", "");
    for (const std::string& page : PAGES)
        parseFile(base + page + "-theme.xml", page);
}

void MadTheme::parseFile(const std::string& path, const std::string& page)
{
    if (!Utils::FileSystem::exists(path))
        return; // No file = built-in look; never an error.

    pugi::xml_document doc;
    const pugi::xml_parse_result result {doc.load_file(path.c_str())};
    if (!result) {
        LOG(LogWarning) << "MadTheme: couldn't parse " << path << " (" << result.description()
                        << ") — using defaults";
        return;
    }
    const pugi::xml_node root {doc.child("madTheme")};
    if (root == nullptr) {
        LOG(LogWarning) << "MadTheme: " << path << " has no <madTheme> root — ignored";
        return;
    }

    // Variables accumulate across files (global file first), so page files
    // can reference the global palette.
    for (pugi::xml_node var : root.child("variables").children())
        mVariables[var.name()] = var.text().as_string();

    auto substitute = [this](std::string value) {
        for (const auto& variable : mVariables) {
            const std::string token {"${" + variable.first + "}"};
            size_t pos {0};
            while ((pos = value.find(token, pos)) != std::string::npos) {
                value.replace(pos, token.length(), variable.second);
                pos += variable.second.length();
            }
        }
        return value;
    };

    for (pugi::xml_node node : root.child("colors").children()) {
        const auto key = COLOR_KEYS.find(node.name());
        if (key == COLOR_KEYS.cend()) {
            LOG(LogWarning) << "MadTheme: unknown color key <" << node.name() << "> in "
                            << path;
            continue;
        }
        std::string hex {substitute(node.text().as_string())};
        if (hex.length() == 6)
            hex += "FF";
        if (hex.length() != 8 ||
            hex.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos) {
            LOG(LogWarning) << "MadTheme: bad color value \"" << hex << "\" for <"
                            << node.name() << "> in " << path;
            continue;
        }
        mColors[page][key->second] =
            static_cast<unsigned int>(std::stoul(hex, nullptr, 16));
    }

    const std::string dir {Utils::FileSystem::getParent(path)};
    for (pugi::xml_node node : root.child("icons").children("icon")) {
        const std::string name {node.attribute("name").as_string()};
        std::string iconPath {substitute(node.text().as_string())};
        if (name.empty() || iconPath.empty())
            continue;
        if (iconPath.rfind("./", 0) == 0)
            iconPath = dir + iconPath.substr(1);
        if (Utils::FileSystem::exists(iconPath))
            mIcons[page][name] = iconPath;
        else
            LOG(LogWarning) << "MadTheme: icon \"" << name << "\" -> " << iconPath
                            << " not found (referenced in " << path << ")";
    }
}

unsigned int MadTheme::color(const MadColor key)
{
    MadTheme& instance {getInstance()};
    const auto pageIt = instance.mColors.find(instance.mActivePage);
    if (pageIt != instance.mColors.cend()) {
        const auto hit = pageIt->second.find(key);
        if (hit != pageIt->second.cend())
            return hit->second;
    }
    const auto globalIt = instance.mColors.find("");
    if (globalIt != instance.mColors.cend()) {
        const auto hit = globalIt->second.find(key);
        if (hit != globalIt->second.cend())
            return hit->second;
    }
    const auto defaultIt = instance.mDefaults.find(key);
    if (defaultIt != instance.mDefaults.cend())
        return defaultIt->second;
    return STOCK.at(key);
}

std::string MadTheme::pageIconPath(const std::string& page, const std::string& name)
{
    MadTheme& instance {getInstance()};
    const auto pageIt = instance.mIcons.find(page);
    if (pageIt != instance.mIcons.cend()) {
        const auto hit = pageIt->second.find(name);
        if (hit != pageIt->second.cend())
            return hit->second;
    }
    const auto globalIt = instance.mIcons.find("");
    if (globalIt != instance.mIcons.cend()) {
        const auto hit = globalIt->second.find(name);
        if (hit != globalIt->second.cend())
            return hit->second;
    }
    return "";
}

std::string MadTheme::iconPath(const std::string& name)
{
    return pageIconPath(getInstance().mActivePage, name);
}
