//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadFolderPicker.cpp
//
//  MAD control panel: gamepad folder browser for a local-backup destination (deck-patches).
//

#include "guis/mad/GuiMadFolderPicker.h"

#include "InputConfig.h"
#include "components/TextComponent.h"
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/MadMsgBox.h"
#include "renderers/Renderer.h"
#include "resources/Font.h"
#include "utils/FileSystemUtil.h"

#include <algorithm>

namespace
{
    constexpr const char* kMediaRoot {"/run/media/deck"};
    constexpr std::string::size_type kPathMax {56}; // truncate the title path to one line
} // namespace

GuiMadFolderPicker::GuiMadFolderPicker(const PickCallback& onPick)
    : mRenderer {Renderer::getInstance()}
    , mOnPick {onPick}
{
    // Roots: Home, then every mounted volume under /run/media/deck (SD card, USB drives).
    const std::string home {Utils::FileSystem::getHomePath()};
    mRoots.emplace_back("Home  (" + home + ")", home);
    if (Utils::FileSystem::isDirectory(kMediaRoot)) {
        Utils::FileSystem::StringList mounts {Utils::FileSystem::getDirContent(kMediaRoot)};
        std::vector<std::string> sorted {mounts.begin(), mounts.end()};
        std::sort(sorted.begin(), sorted.end());
        for (const std::string& mount : sorted) {
            if (Utils::FileSystem::isDirectory(mount))
                mRoots.emplace_back(Utils::FileSystem::getFileName(mount) + "  (" + mount + ")",
                                    mount);
        }
    }

    setSize(Renderer::getScreenWidth(), Renderer::getScreenHeight());
    buildMenu();
}

void GuiMadFolderPicker::buildMenu()
{
    // ComponentList has no clear(), so rebuild the whole menu on each navigation. removeChild the
    // old one BEFORE the unique_ptr assignment frees it, so addChild never holds a dangling ptr.
    if (mMenu)
        removeChild(mMenu.get());

    mMenu = std::make_unique<MenuComponent>(atRoots() ? std::string {"PICK A BACKUP DESTINATION"}
                                                      : shortPath(mCurrent));

    auto addDirRow = [this](const std::string& label, const std::function<void()>& onAccept,
                            bool cursorHere) {
        ComponentListRow row;
        row.addElement(std::make_shared<TextComponent>(label, Font::get(FONT_SIZE_MEDIUM),
                                                       mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
                                                       glm::ivec2 {0, 0}),
                       true);
        row.makeAcceptInputHandler(onAccept);
        mMenu->addRow(row, cursorHere);
    };

    if (atRoots()) {
        for (const auto& root : mRoots) {
            const std::string path {root.second};
            addDirRow(root.first, [this, path] { enter(path); }, false);
        }
    }
    else {
        addDirRow(".. (up)", [this] { goUp(); }, true); // cursor starts on "up"
        // Subdirectories only (files can't be a destination), dot-folders hidden, sorted.
        Utils::FileSystem::StringList kids {Utils::FileSystem::getDirContent(mCurrent)};
        std::vector<std::string> dirs;
        for (const std::string& kid : kids) {
            const std::string name {Utils::FileSystem::getFileName(kid)};
            if (!name.empty() && name.front() != '.' && Utils::FileSystem::isDirectory(kid))
                dirs.push_back(kid);
        }
        std::sort(dirs.begin(), dirs.end());
        for (const std::string& dir : dirs) {
            const std::string path {dir};
            addDirRow(Utils::FileSystem::getFileName(dir), [this, path] { enter(path); }, false);
        }
    }

    // USE THIS FOLDER / NEW FOLDER only make sense inside a real directory (not the roots list).
    if (!atRoots()) {
        mMenu->addButton("USE THIS FOLDER", "use this folder", [this] { pick(mCurrent); });
        mMenu->addButton("NEW FOLDER", "new folder", [this] { promptNewFolder(); });
    }
    mMenu->addButton("CANCEL", "cancel", [this] { cancel(); });

    addChild(mMenu.get());
    mMenu->setPosition(std::round((mSize.x - mMenu->getSize().x) / 2.0f),
                       std::round(Renderer::getScreenHeight() * 0.13f));
}

void GuiMadFolderPicker::enter(const std::string& dir)
{
    mCurrent = dir;
    buildMenu();
}

void GuiMadFolderPicker::goUp()
{
    for (const auto& root : mRoots) {
        if (mCurrent == root.second) { // at a drive root -> back to the roots list
            mCurrent.clear();
            buildMenu();
            return;
        }
    }
    const std::string parent {Utils::FileSystem::getParent(mCurrent)};
    mCurrent = (parent == mCurrent) ? std::string {} : parent; // guard the fs-root fixpoint
    buildMenu();
}

void GuiMadFolderPicker::promptNewFolder()
{
    const std::string base {mCurrent};
    mWindow->pushGui(new GuiTextEditKeyboardPopup(
        0.0f, "NEW FOLDER NAME", "",
        [this, base](const std::string& name) {
            std::string clean {name};
            const std::string ws {" \t"};
            clean.erase(0, clean.find_first_not_of(ws));
            const auto last {clean.find_last_not_of(ws)};
            clean.erase(last == std::string::npos ? 0 : last + 1);
            if (clean.empty() || clean == "." || clean == ".." ||
                clean.find('/') != std::string::npos) {
                mWindow->pushGui(new MadMsgBox("Enter a simple folder name (no slashes).", "OK"));
                return;
            }
            const std::string path {base + "/" + clean};
            if (!Utils::FileSystem::createDirectory(path)) {
                mWindow->pushGui(new MadMsgBox("Couldn't create:\n" + path, "OK"));
                return;
            }
            enter(path); // step into the new folder, ready to USE THIS FOLDER
        },
        false, "CREATE", "SAVE?", "Type a name for the new folder in " + shortPath(base), "",
        "LOAD DEFAULT", "CLEAR", "CANCEL", false));
}

void GuiMadFolderPicker::pick(std::string path)
{
    // deleteMeAndCall pattern (GuiMadCaptureModal::finish): pop first, then run the callback -
    // it re-enters the Backup page below, which must not see this modal still on the stack.
    // `path` is taken BY VALUE so the caller (pick(mCurrent)) copies it before delete this frees
    // mCurrent - the callback must never read a member of the just-destroyed picker.
    const PickCallback callback {mOnPick};
    delete this;
    if (callback)
        callback(path);
}

void GuiMadFolderPicker::cancel()
{
    const PickCallback callback {mOnPick};
    delete this;
    if (callback)
        callback("");
}

bool GuiMadFolderPicker::input(InputConfig* config, Input input)
{
    if (input.device == DEVICE_KEYBOARD)
        return true; // MAD convention: the keyboard never drives the panel (Sinden gun safety).
    if (config->isMappedTo("b", input) && input.value != 0) {
        if (atRoots())
            cancel(); // deletes this
        else
            goUp();
        return true;
    }
    return GuiComponent::input(config, input); // up/down/A + the buttons -> mMenu
}

std::vector<HelpPrompt> GuiMadFolderPicker::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {mMenu ? mMenu->getHelpPrompts() : std::vector<HelpPrompt> {}};
    prompts.push_back(HelpPrompt("b", atRoots() ? "cancel" : "up"));
    return prompts;
}

std::string GuiMadFolderPicker::shortPath(const std::string& path) const
{
    if (path.length() <= kPathMax)
        return path;
    return "..." + path.substr(path.length() - (kPathMax - 3));
}
