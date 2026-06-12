//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadTheme.h
//
//  MAD panel theming (deck-patches): colors + icons from the ACTIVE ES-DE
//  theme's router-config/ dir — `mad-theme.xml` (global) plus per-page
//  `<pagename>-theme.xml` overrides (pagename = the panel's artKey, e.g.
//  quit-combo-theme.xml). Lookup order: active page → global → the runtime
//  menu-scheme defaults the panel injects → stock constant. No theme files =
//  pixel-identical to the untheme look. Schema (pugixml, ${var} substitution
//  incl. the "${accent}40" alpha-suffix idiom of the reference theme.xml):
//
//    <madTheme>
//      <variables><accent>fda504</accent></variables>
//      <colors><primary>${accent}</primary><selector>f6e772</selector>…</colors>
//      <icons><icon name="sidebar">./icons/preview.png</icon>…</icons>
//    </madTheme>
//

#ifndef ES_APP_GUIS_MAD_MAD_THEME_H
#define ES_APP_GUIS_MAD_MAD_THEME_H

#include <map>
#include <string>

enum class MadColor {
    Frame,
    Primary,
    Secondary,
    Title,
    Selector,
    Red,
    Green,
    Separators,
    PanelDimmed,
    ButtonFlatUnfocused,
    HelpText,
};

class MadTheme
{
public:
    static MadTheme& getInstance();

    // (Re)load from the active ES-DE theme. `defaults` = the CURRENT menu
    // scheme values (the panel reads its own GuiComponent statics — they are
    // protected, and this also tracks the dark/light scheme correctly).
    void load(const std::map<MadColor, unsigned int>& defaults);
    void setActivePage(const std::string& page) { mActivePage = page; }

    // Lookup: active page → global → injected defaults → stock constant.
    static unsigned int color(const MadColor key);
    // Themed icon for the active page (or global), absolute path; "" = use
    // the regular backend art chain.
    static std::string iconPath(const std::string& name);
    static std::string pageIconPath(const std::string& page, const std::string& name);

private:
    MadTheme() {}
    void parseFile(const std::string& path, const std::string& page);

    std::map<MadColor, unsigned int> mDefaults;
    // page ("" = global) → key → value.
    std::map<std::string, std::map<MadColor, unsigned int>> mColors;
    std::map<std::string, std::map<std::string, std::string>> mIcons;
    std::map<std::string, std::string> mVariables;
    std::string mActivePage;
};

#endif // ES_APP_GUIS_MAD_MAD_THEME_H
