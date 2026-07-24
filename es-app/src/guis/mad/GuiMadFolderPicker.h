//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadFolderPicker.h
//
//  MAD control panel: a gamepad folder browser for choosing a local-backup destination
//  (deck-patches). ES-DE has no filesystem picker, so this builds one on MenuComponent:
//  it opens at the drive roots (Home + every mounted volume under /run/media/deck), drills
//  into subfolders (A to enter, B to go up / cancel at the roots), can make a new folder,
//  and returns the chosen ABSOLUTE path through the callback (empty string == cancelled).
//

#ifndef ES_APP_GUIS_MAD_GUI_MAD_FOLDER_PICKER_H
#define ES_APP_GUIS_MAD_GUI_MAD_FOLDER_PICKER_H

#include "GuiComponent.h"
#include "components/MenuComponent.h"

#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

class GuiMadFolderPicker : public GuiComponent
{
public:
    using PickCallback = std::function<void(const std::string& path)>;

    explicit GuiMadFolderPicker(const PickCallback& onPick);

    bool input(InputConfig* config, Input input) override;
    std::vector<HelpPrompt> getHelpPrompts() override;

private:
    void buildMenu();                     // (re)build mMenu for mCurrent ("" == the roots list)
    void enter(const std::string& dir);   // descend into dir
    void goUp();                          // parent dir, or back up to the roots list
    void promptNewFolder();               // keyboard -> createDirectory -> step into it
    void pick(std::string path);          // fire the callback(path) + close (BY VALUE: pick(mCurrent)
                                          // must copy before delete this frees mCurrent)
    void cancel();                        // fire the callback("") + close
    bool atRoots() const { return mCurrent.empty(); }
    std::string shortPath(const std::string& path) const; // truncate a long path for the title

    Renderer* mRenderer;
    std::unique_ptr<MenuComponent> mMenu;
    PickCallback mOnPick;
    std::string mCurrent;                                    // "" == the roots list
    std::vector<std::pair<std::string, std::string>> mRoots; // (label, absolute path)
};

#endif // ES_APP_GUIS_MAD_GUI_MAD_FOLDER_PICKER_H
