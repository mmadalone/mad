//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageQuitCombo.cpp
//
//  MAD control panel: Quit-game combo section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageQuitCombo.h"

#include "Sound.h"
#include "Window.h"
#include "guis/mad/GuiMadCaptureModal.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "utils/StringUtil.h"

#include <cstdio>

namespace
{
    std::string joinNames(const std::vector<std::string>& names)
    {
        if (names.empty())
            return "(none)";
        std::string out;
        for (size_t i {0}; i < names.size(); ++i) {
            if (i > 0)
                out.append("+");
            out.append(names[i]);
        }
        return out;
    }

    std::string formatHold(const float value)
    {
        char buffer[16];
        snprintf(buffer, sizeof(buffer), "%.1f", value);
        return std::string {buffer};
    }
} // namespace

GuiMadPageQuitCombo::GuiMadPageQuitCombo(GuiMadPanel* panel)
    : MadPage {panel, "QUIT-GAME COMBO"}
    , mHold {1.0f}
    , mFocusTarget {FocusAdd}
    , mGridCookie {0}
    , mBuilt {false}
{
}

void GuiMadPageQuitCombo::build()
{
    setLoadingText("Loading quit combos…");

    // Console art first (one-time), then the page data.
    pageRequest(
        "systems.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            if (ok) {
                const rapidjson::Value& systems {MadJson::getMember(payload, "systems")};
                if (systems.IsArray()) {
                    for (rapidjson::SizeType i {0}; i < systems.Size(); ++i) {
                        mSystemArt[MadJson::getString(systems[i], "name")] =
                            MadJson::getString(systems[i], "art");
                    }
                }
            }
            refreshData();
        },
        10000);
}

void GuiMadPageQuitCombo::refreshData(const bool keepUnsaved)
{
    pageRequest("quitcombo.get", nullptr,
                [this, keepUnsaved](bool ok, const rapidjson::Value& payload) {
                    setLoadingText("");
                    if (!ok) {
                        footer()->setStatus(
                            "Couldn't load the quit combos: " +
                                MadJson::getString(payload, "message", "unknown error"),
                            true);
                        return;
                    }
                    rebuild(payload, keepUnsaved);
                });
}

void GuiMadPageQuitCombo::onChildPopped()
{
    // The picker/detail pages write per-system overrides; rebuild from truth —
    // but keep the in-memory global combo/hold time: an unsaved DETECT result
    // must survive a trip into the per-system pages.
    refreshData(true);
}

std::string GuiMadPageQuitCombo::comboString() const
{
    return joinNames(mComboNames);
}

void GuiMadPageQuitCombo::clearLayout()
{
    const std::vector<GuiComponent*> components {
        mIntro.get(),     mGlobalHeader.get(), mComboLine.get(),   mStepper.get(),
        mDetectButton.get(), mSaveButton.get(), mPerSystemHeader.get(), mWiiNote.get(),
        mAddButton.get(), mNoOverrides.get(),  mGrid.get()};
    for (GuiComponent* component : components) {
        if (component != nullptr)
            removeChild(component);
    }
    mIntro.reset();
    mGlobalHeader.reset();
    mComboLine.reset();
    mStepper.reset();
    mDetectButton.reset();
    mSaveButton.reset();
    mPerSystemHeader.reset();
    mWiiNote.reset();
    mAddButton.reset();
    mNoOverrides.reset();
    mGrid.reset();
}

void GuiMadPageQuitCombo::rebuild(const rapidjson::Value& result, const bool keepUnsaved)
{
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
    clearLayout();

    // ── data ──
    // keepUnsaved (post-child-pop refresh): the global combo/hold time fields
    // may hold an unsaved DETECT/stepper edit — only the overrides re-read.
    if (!keepUnsaved) {
        mComboButtons.clear();
        mComboNames.clear();
        const rapidjson::Value& buttons {MadJson::getMember(result, "buttons")};
        if (buttons.IsArray()) {
            for (rapidjson::SizeType i {0}; i < buttons.Size(); ++i) {
                if (buttons[i].IsInt())
                    mComboButtons.emplace_back(buttons[i].GetInt());
            }
        }
        const rapidjson::Value& names {MadJson::getMember(result, "names")};
        if (names.IsArray()) {
            for (rapidjson::SizeType i {0}; i < names.Size(); ++i) {
                if (names[i].IsString())
                    mComboNames.emplace_back(names[i].GetString());
            }
        }
        const rapidjson::Value& hold {MadJson::getMember(result, "hold_sec")};
        mHold = hold.IsNumber() ? static_cast<float>(hold.GetDouble()) : 1.0f;
    }

    mOverrides.clear();
    const rapidjson::Value& overrides {MadJson::getMember(result, "overrides")};
    if (overrides.IsObject()) {
        for (auto it = overrides.MemberBegin(); it != overrides.MemberEnd(); ++it) {
            std::vector<std::string> overrideNames;
            const rapidjson::Value& list {MadJson::getMember(it->value, "names")};
            if (list.IsArray()) {
                for (rapidjson::SizeType i {0}; i < list.Size(); ++i) {
                    if (list[i].IsString())
                        overrideNames.emplace_back(list[i].GetString());
                }
            }
            mOverrides.emplace_back(it->name.GetString(), joinNames(overrideNames));
        }
    }

    // ── layout ──
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float miniHeight {Font::get(FONT_SIZE_MINI)->getHeight()};
    float y {mViewportPos.y};

    mIntro = std::make_shared<TextComponent>(
        "Hold a gamepad combo ~1s to quit a standalone game → ES-DE. Eligible systems are "
        "auto-discovered from ES-DE (standalone emulators you have games for).",
        Font::get(FONT_SIZE_SMALL), mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    mIntro->setPosition(mViewportPos.x, y);
    mIntro->setSize(mViewportSize.x, 0.0f);
    addChild(mIntro.get());
    y += mIntro->getSize().y + smallHeight * 0.3f;

    mGlobalHeader = std::make_shared<TextComponent>("Global default", Font::get(FONT_SIZE_SMALL),
                                                    mMenuColorTitle, ALIGN_LEFT, ALIGN_CENTER,
                                                    glm::ivec2 {0, 0});
    mGlobalHeader->setPosition(mViewportPos.x, y);
    mGlobalHeader->setSize(mViewportSize.x, smallHeight);
    addChild(mGlobalHeader.get());
    y += smallHeight;

    const float largeHeight {Font::get(FONT_SIZE_LARGE)->getHeight()};
    mComboLine = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_LARGE),
                                                 mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
                                                 glm::ivec2 {0, 0});
    mComboLine->setPosition(mViewportPos.x, y);
    mComboLine->setSize(mViewportSize.x, largeHeight);
    addChild(mComboLine.get());
    y += largeHeight + smallHeight * 0.2f;

    mStepper = std::make_shared<MadStepper>(
        "hold time (s)", 0.3f, 3.0f, 0.1f, [](const float value) { return formatHold(value); },
        [this](const float value) {
            mHold = value;
            refreshComboLine();
        });
    mStepper->setPosition(mViewportPos.x, y);
    mStepper->setSize(mViewportSize.x * 0.45f, Font::get(FONT_SIZE_MEDIUM)->getHeight() * 1.4f);
    mStepper->setValue(mHold);
    addChild(mStepper.get());
    y += mStepper->getSize().y + smallHeight * 0.3f;

    mDetectButton =
        std::make_shared<ButtonComponent>("DETECT", "detect", [this] { detectGlobal(); });
    mDetectButton->setPosition(mViewportPos.x, y);
    addChild(mDetectButton.get());
    mSaveButton = std::make_shared<ButtonComponent>("SAVE", "save", [this] { saveGlobal(); });
    mSaveButton->setPosition(mViewportPos.x + mDetectButton->getSize().x +
                                 mViewportSize.x * 0.012f,
                             y);
    addChild(mSaveButton.get());
    y += mDetectButton->getSize().y + smallHeight * 0.5f;

    mPerSystemHeader = std::make_shared<TextComponent>(
        "Per system (overrides the global)", Font::get(FONT_SIZE_SMALL), mMenuColorTitle,
        ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mPerSystemHeader->setPosition(mViewportPos.x, y);
    mPerSystemHeader->setSize(mViewportSize.x, smallHeight);
    addChild(mPerSystemHeader.get());
    y += smallHeight;

    mWiiNote = std::make_shared<TextComponent>(
        "wii: + & −  (real Wii Remotes via DolphinBar — HID, fixed)", Font::get(FONT_SIZE_MINI),
        mMenuColorSecondary, ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mWiiNote->setPosition(mViewportPos.x, y);
    mWiiNote->setSize(mViewportSize.x, miniHeight);
    addChild(mWiiNote.get());
    y += miniHeight + smallHeight * 0.3f;

    mAddButton = std::make_shared<ButtonComponent>(
        "ADD PER-SYSTEM COMBO", "add per-system combo",
        [this] { mPanel->pushPage(new GuiMadPageQuitComboPicker(mPanel)); });
    mAddButton->setPosition(mViewportPos.x, y);
    addChild(mAddButton.get());
    y += mAddButton->getSize().y + smallHeight * 0.3f;

    if (mOverrides.empty()) {
        mNoOverrides = std::make_shared<TextComponent>(
            "  (none — every system uses the global combo)", Font::get(FONT_SIZE_SMALL),
            mMenuColorSecondary, ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
        mNoOverrides->setPosition(mViewportPos.x, y);
        mNoOverrides->setSize(mViewportSize.x, smallHeight);
        addChild(mNoOverrides.get());
    }
    else {
        std::vector<MadTileGrid::Tile> tiles;
        for (const auto& entry : mOverrides) {
            MadTileGrid::Tile tile;
            tile.key = entry.first;
            tile.label = entry.first;
            tile.sublabel = entry.second;
            const auto art = mSystemArt.find(entry.first);
            if (art != mSystemArt.end())
                tile.artPath = art->second;
            tiles.emplace_back(tile);
        }
        mGrid = std::make_shared<MadTileGrid>();
        mGrid->setPosition(mViewportPos.x, y);
        mGrid->setSize(mViewportSize.x,
                       std::max(mViewportPos.y + mViewportSize.y - y, smallHeight * 2.0f));
        mGrid->setTiles(tiles);
        mGrid->setOnPick([this](const std::string& system) {
            std::string comboNames;
            for (const auto& entry : mOverrides) {
                if (entry.first == system)
                    comboNames = entry.second;
            }
            std::string artPath;
            const auto art = mSystemArt.find(system);
            if (art != mSystemArt.end())
                artPath = art->second;
            mPanel->pushPage(new GuiMadPageQuitComboDetail(mPanel, system, comboNames, artPath));
        });
        mGrid->setCursorIndex(mGridCookie);
        addChild(mGrid.get());
    }

    refreshComboLine();
    mBuilt = true;
    if (mFocusTarget == FocusGrid && mGrid == nullptr)
        mFocusTarget = FocusAdd;
    setFocusTarget(mFocusTarget);
}

void GuiMadPageQuitCombo::refreshComboLine()
{
    if (mComboLine != nullptr)
        mComboLine->setText("  " + comboString() + "   ·   hold " + formatHold(mHold) + "s");
}

void GuiMadPageQuitCombo::detectGlobal()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "combo", "Hold the combo, then release…",
        [this, alive](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr || result->held.empty())
                return;
            mComboButtons = result->held;
            mComboNames = result->names;
            refreshComboLine();
            footer()->setStatus("Captured " + std::to_string(result->held.size()) +
                                " button(s) — press Save.");
        }));
}

void GuiMadPageQuitCombo::saveGlobal()
{
    const std::vector<int> buttons {mComboButtons};
    const float holdSec {mHold};
    pageRequest(
        "policy.set_quit_combo",
        [buttons, holdSec](MadJson::Writer& writer) {
            writer.Key("scope");
            writer.Null();
            writer.Key("buttons");
            writer.StartArray();
            for (const int button : buttons)
                writer.Int(button);
            writer.EndArray();
            writer.Key("hold_sec");
            writer.Double(static_cast<double>(holdSec));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save the global combo: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash("Saved global combo (" + comboString() + " · hold " +
                            formatHold(mHold) + "s)");
        });
}

void GuiMadPageQuitCombo::setFocusTarget(const int target)
{
    mFocusTarget = target;
    if (mStepper != nullptr) {
        if (target == FocusStepper)
            mStepper->onFocusGained();
        else
            mStepper->onFocusLost();
    }
    auto applyButton = [target](const std::shared_ptr<ButtonComponent>& button,
                                const int focusId) {
        if (button == nullptr)
            return;
        if (target == focusId)
            button->onFocusGained();
        else
            button->onFocusLost();
    };
    applyButton(mDetectButton, FocusDetect);
    applyButton(mSaveButton, FocusSave);
    applyButton(mAddButton, FocusAdd);
    mPanel->refreshHelpPrompts();
}

bool GuiMadPageQuitCombo::input(InputConfig* config, Input input)
{
    if (!mBuilt)
        return false;

    if (mFocusTarget == FocusStepper) {
        if (mStepper->input(config, input))
            return true;
        if (input.value != 0 && config->isMappedLike("down", input)) {
            setFocusTarget(FocusDetect);
            return true;
        }
        if (input.value != 0 && config->isMappedLike("up", input))
            return true; // Top edge: nothing above.
        return false;
    }

    if (mFocusTarget == FocusDetect || mFocusTarget == FocusSave) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("left", input)) {
            if (mFocusTarget == FocusSave) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                setFocusTarget(FocusDetect);
            }
            return true;
        }
        if (config->isMappedLike("right", input)) {
            if (mFocusTarget == FocusDetect) {
                NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
                setFocusTarget(FocusSave);
            }
            return true;
        }
        if (config->isMappedLike("up", input)) {
            setFocusTarget(FocusStepper);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            setFocusTarget(FocusAdd);
            return true;
        }
        if (config->isMappedTo("a", input)) {
            return mFocusTarget == FocusDetect ? mDetectButton->input(config, input) :
                                                 mSaveButton->input(config, input);
        }
        return false;
    }

    if (mFocusTarget == FocusAdd) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("up", input)) {
            setFocusTarget(FocusDetect);
            return true;
        }
        if (config->isMappedLike("down", input)) {
            if (mGrid != nullptr)
                setFocusTarget(FocusGrid);
            return true;
        }
        if (config->isMappedTo("a", input))
            return mAddButton->input(config, input);
        return false;
    }

    // FocusGrid.
    if (mGrid == nullptr) {
        setFocusTarget(FocusAdd);
        return true;
    }
    if (input.value != 0 && config->isMappedLike("up", input)) {
        const int before {mGrid->cursorIndex()};
        mGrid->input(config, input);
        if (mGrid->cursorIndex() == before)
            setFocusTarget(FocusAdd); // Already on the top row.
        return true;
    }
    return mGrid->input(config, input);
}

void GuiMadPageQuitCombo::pageScroll(int direction)
{
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageQuitCombo::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt)
        return prompts;
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        return mGrid->getHelpPrompts();
    prompts.push_back(HelpPrompt("up/down", "choose"));
    if (mFocusTarget == FocusStepper)
        prompts.push_back(HelpPrompt("left/right", "adjust"));
    else
        prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}

void GuiMadPageQuitCombo::onSaveFocus()
{
    mFocusCookie = mFocusTarget;
    if (mGrid != nullptr)
        mGridCookie = mGrid->cursorIndex();
}

void GuiMadPageQuitCombo::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocusTarget(mFocusCookie);
    if (mFocusTarget == FocusGrid && mGrid != nullptr)
        mGrid->setCursorIndex(mGridCookie);
}

//  ── GuiMadPageQuitComboPicker ──

GuiMadPageQuitComboPicker::GuiMadPageQuitComboPicker(GuiMadPanel* panel)
    : MadPage {panel, "ADD PER-SYSTEM QUIT COMBO"}
{
}

void GuiMadPageQuitComboPicker::build()
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mIntro = std::make_shared<TextComponent>(
        "Pick a system, then hold the combo you want (~1s, then release).",
        Font::get(FONT_SIZE_SMALL), mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 0});
    mIntro->setPosition(mViewportPos.x, mViewportPos.y);
    mIntro->setSize(mViewportSize.x, smallHeight);
    addChild(mIntro.get());

    setLoadingText("Loading systems…");
    pageRequest(
        "systems.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            std::map<std::string, std::string> art;
            if (ok) {
                const rapidjson::Value& systems {MadJson::getMember(payload, "systems")};
                if (systems.IsArray()) {
                    for (rapidjson::SizeType i {0}; i < systems.Size(); ++i) {
                        art[MadJson::getString(systems[i], "name")] =
                            MadJson::getString(systems[i], "art");
                    }
                }
            }
            pageRequest("quitcombo.get", nullptr,
                        [this, art](bool ok, const rapidjson::Value& payload) {
                            setLoadingText("");
                            if (!ok) {
                                footer()->setStatus(
                                    "Couldn't load the quit combos: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                                return;
                            }
                            // Eligible minus already-overridden.
                            const rapidjson::Value& overrides {
                                MadJson::getMember(payload, "overrides")};
                            std::vector<MadTileGrid::Tile> tiles;
                            const rapidjson::Value& eligible {
                                MadJson::getMember(payload, "eligible")};
                            if (eligible.IsArray()) {
                                for (rapidjson::SizeType i {0}; i < eligible.Size(); ++i) {
                                    if (!eligible[i].IsString())
                                        continue;
                                    const std::string system {eligible[i].GetString()};
                                    if (overrides.IsObject() &&
                                        overrides.HasMember(system.c_str()))
                                        continue;
                                    MadTileGrid::Tile tile;
                                    tile.key = system;
                                    tile.label = system;
                                    const auto it = art.find(system);
                                    if (it != art.end())
                                        tile.artPath = it->second;
                                    tiles.emplace_back(tile);
                                }
                            }

                            if (tiles.empty()) {
                                setLoadingText(
                                    "All eligible systems already have an override.");
                                return;
                            }

                            const float top {mIntro->getPosition().y + mIntro->getSize().y +
                                             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
                            mGrid = std::make_shared<MadTileGrid>();
                            mGrid->setPosition(mViewportPos.x, top);
                            mGrid->setSize(mViewportSize.x,
                                           mViewportPos.y + mViewportSize.y - top);
                            mGrid->setTiles(tiles);
                            mGrid->setOnPick(
                                [this](const std::string& system) { armCapture(system); });
                            mGrid->setCursorIndex(mFocusCookie);
                            addChild(mGrid.get());
                            mPanel->refreshHelpPrompts();
                        });
        },
        10000);
}

void GuiMadPageQuitComboPicker::armCapture(const std::string& system)
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "combo", "Hold the combo for " + system + ", then release…",
        [this, alive, system](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr || result->held.empty())
                return;
            const std::vector<int> buttons {result->held};
            pageRequest(
                "policy.set_quit_combo",
                [system, buttons](MadJson::Writer& writer) {
                    writer.Key("scope");
                    writer.String(system.c_str(),
                                  static_cast<rapidjson::SizeType>(system.length()));
                    writer.Key("buttons");
                    writer.StartArray();
                    for (const int button : buttons)
                        writer.Int(button);
                    writer.EndArray();
                },
                [this, system](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        footer()->flash(
                            "Couldn't save the " + system + " combo: " +
                                MadJson::getString(payload, "message", "unknown error"),
                            4000, true);
                        return;
                    }
                    footer()->flash("Saved " + system + " combo");
                    mPanel->popPage(); // Back to the root page (it rebuilds).
                });
        }));
}

bool GuiMadPageQuitComboPicker::input(InputConfig* config, Input input)
{
    if (mGrid != nullptr)
        return mGrid->input(config, input);
    return false;
}

void GuiMadPageQuitComboPicker::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageQuitComboPicker::getHelpPrompts()
{
    if (mGrid != nullptr)
        return mGrid->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPageQuitComboPicker::onSaveFocus()
{
    if (mGrid != nullptr)
        mFocusCookie = mGrid->cursorIndex();
}

void GuiMadPageQuitComboPicker::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mFocusCookie);
}

//  ── GuiMadPageQuitComboDetail ──

GuiMadPageQuitComboDetail::GuiMadPageQuitComboDetail(GuiMadPanel* panel,
                                                     const std::string& system,
                                                     const std::string& comboNames,
                                                     const std::string& artPath)
    : MadPage {panel, "QUIT COMBO: " + Utils::String::toUpper(system)}
    , mSystem {system}
    , mComboNames {comboNames}
    , mArtPath {artPath}
    , mButtonFocus {0}
{
}

void GuiMadPageQuitComboDetail::build()
{
    float y {mViewportPos.y};

    if (!mArtPath.empty()) {
        const float artHeight {mViewportSize.y * 0.24f};
        mArt = std::make_shared<ImageComponent>();
        mArt->setOrigin(0.5f, 0.5f);
        mArt->setMaxSize(mViewportSize.x * 0.4f, artHeight);
        mArt->setImage(mArtPath);
        mArt->setPosition(mViewportPos.x + mViewportSize.x / 2.0f, y + artHeight / 2.0f);
        addChild(mArt.get());
        y += artHeight + mViewportSize.y * 0.03f;
    }

    const float largeHeight {Font::get(FONT_SIZE_LARGE)->getHeight()};
    mComboLine = std::make_shared<TextComponent>("Override combo:  " + mComboNames,
                                                 Font::get(FONT_SIZE_LARGE), mMenuColorPrimary,
                                                 ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
    mComboLine->setPosition(mViewportPos.x, y);
    mComboLine->setSize(mViewportSize.x, largeHeight);
    addChild(mComboLine.get());
    y += largeHeight + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.6f;

    mRedetectButton =
        std::make_shared<ButtonComponent>("RE-DETECT", "re-detect", [this] { redetect(); });
    mRedetectButton->setPosition(mViewportPos.x, y);
    addChild(mRedetectButton.get());

    mClearButton = std::make_shared<ButtonComponent>("CLEAR OVERRIDE", "clear override",
                                                     [this] { clearOverride(); });
    mClearButton->setPosition(mViewportPos.x + mRedetectButton->getSize().x +
                                  mViewportSize.x * 0.012f,
                              y);
    addChild(mClearButton.get());

    applyButtonFocus();
}

void GuiMadPageQuitComboDetail::applyButtonFocus()
{
    if (mButtonFocus == 0) {
        mRedetectButton->onFocusGained();
        mClearButton->onFocusLost();
    }
    else {
        mRedetectButton->onFocusLost();
        mClearButton->onFocusGained();
    }
}

void GuiMadPageQuitComboDetail::redetect()
{
    std::weak_ptr<int> alive {pageAlive()};
    const std::string system {mSystem};
    mWindow->pushGui(new GuiMadCaptureModal(
        mPanel, "combo", "Hold the combo for " + system + ", then release…",
        [this, alive, system](const GuiMadCaptureModal::Result* result) {
            if (alive.expired() || result == nullptr || result->held.empty())
                return;
            const std::vector<int> buttons {result->held};
            const std::string comboNames {joinNames(result->names)};
            pageRequest(
                "policy.set_quit_combo",
                [system, buttons](MadJson::Writer& writer) {
                    writer.Key("scope");
                    writer.String(system.c_str(),
                                  static_cast<rapidjson::SizeType>(system.length()));
                    writer.Key("buttons");
                    writer.StartArray();
                    for (const int button : buttons)
                        writer.Int(button);
                    writer.EndArray();
                },
                [this, system, comboNames](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        footer()->flash(
                            "Couldn't save the " + system + " combo: " +
                                MadJson::getString(payload, "message", "unknown error"),
                            4000, true);
                        return;
                    }
                    mComboNames = comboNames;
                    mComboLine->setText("Override combo:  " + mComboNames);
                    footer()->flash("Saved " + system + " combo");
                });
        }));
}

void GuiMadPageQuitComboDetail::clearOverride()
{
    const std::string system {mSystem};
    pageRequest(
        "policy.clear_quit_combo",
        [system](MadJson::Writer& writer) {
            writer.Key("system");
            writer.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
        },
        [this, system](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't clear the " + system + " override: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            footer()->flash("Override cleared — " + system + " uses the global combo");
            mPanel->popPage(); // 'this' dies here; nothing below touches members.
        });
}

bool GuiMadPageQuitComboDetail::input(InputConfig* config, Input input)
{
    if (input.value == 0)
        return false;

    if (config->isMappedLike("left", input)) {
        if (mButtonFocus == 1) {
            mButtonFocus = 0;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyButtonFocus();
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mButtonFocus == 0) {
            mButtonFocus = 1;
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            applyButtonFocus();
        }
        return true;
    }
    if (config->isMappedTo("a", input)) {
        return mButtonFocus == 0 ? mRedetectButton->input(config, input) :
                                   mClearButton->input(config, input);
    }
    return false;
}

std::vector<HelpPrompt> GuiMadPageQuitComboDetail::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    prompts.push_back(HelpPrompt("left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}
