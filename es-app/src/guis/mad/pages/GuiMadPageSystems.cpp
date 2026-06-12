//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSystems.cpp
//
//  MAD control panel: Systems section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageSystems.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "utils/StringUtil.h"

GuiMadPageSystems::GuiMadPageSystems(GuiMadPanel* panel)
    : MadPage {panel, "SYSTEMS"}
{
}

void GuiMadPageSystems::build()
{
    setLoadingText("Loading systems…");
    requestSystems();
}

void GuiMadPageSystems::onChildPopped()
{
    // A detail-page toggle may have changed the ● badge/sublabel truth; rebuild
    // the grid from fresh data. The old grid stays visible until the response
    // arrives; the focus cookie restores the cursor on the new grid.
    requestSystems();
}

void GuiMadPageSystems::requestSystems()
{
    pageRequest(
        "systems.list", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't list systems: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }

            std::vector<MadTileGrid::Tile> tiles;
            const rapidjson::Value& systems {MadJson::getMember(payload, "systems")};
            if (systems.IsArray()) {
                for (rapidjson::SizeType i {0}; i < systems.Size(); ++i) {
                    const rapidjson::Value& entry {systems[i]};
                    MadTileGrid::Tile tile;
                    tile.key = MadJson::getString(entry, "name");
                    tile.label = tile.key;
                    tile.sublabel = MadJson::getString(entry, "sub");
                    tile.artPath = MadJson::getString(entry, "art");
                    tile.badge = MadJson::getBool(entry, "configured");
                    tiles.emplace_back(tile);
                }
            }

            if (mGrid != nullptr) {
                // Rebuild (after a detail-page pop): save the cursor, then swap
                // the grid out from under it.
                mFocusCookie = mGrid->cursorIndex();
                removeChild(mGrid.get());
                mGrid.reset();
            }

            mGrid = std::make_shared<MadTileGrid>();
            mGrid->setPosition(mViewportPos.x, mViewportPos.y);
            mGrid->setSize(mViewportSize);
            mGrid->setTiles(tiles);
            mGrid->setOnPick([this](const std::string& system) {
                mPanel->pushPage(new GuiMadPageSystemDetail(mPanel, system));
            });
            addChild(mGrid.get());
            mGrid->setCursorIndex(mFocusCookie);
            mGrid->onFocusGained(); // The grid is this page's only focusable.

            footer()->setStatus(std::to_string(tiles.size()) +
                                " systems — ● = locally configured");
            mPanel->refreshHelpPrompts();
        },
        10000);
}

bool GuiMadPageSystems::input(InputConfig* config, Input input)
{
    if (mGrid != nullptr)
        return mGrid->input(config, input);
    return false;
}

void GuiMadPageSystems::pageScroll(int direction)
{
    if (mGrid != nullptr)
        mGrid->pageScroll(direction);
}

std::vector<HelpPrompt> GuiMadPageSystems::getHelpPrompts()
{
    if (mGrid != nullptr)
        return mGrid->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPageSystems::onSaveFocus()
{
    if (mGrid != nullptr)
        mFocusCookie = mGrid->cursorIndex();
}

void GuiMadPageSystems::onRestoreFocus()
{
    if (mGrid != nullptr)
        mGrid->setCursorIndex(mFocusCookie);
}

GuiMadPageSystemDetail::GuiMadPageSystemDetail(GuiMadPanel* panel, const std::string& system)
    : MadPage {panel, Utils::String::toUpper(system)}
    , mSystem {system}
{
}

void GuiMadPageSystemDetail::build()
{
    setLoadingText("Loading " + mSystem + "…");

    const std::string system {mSystem};
    pageRequest(
        "systems.get",
        [system](MadJson::Writer& writer) {
            writer.Key("system");
            writer.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load " + mSystem + ": " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            populate(payload);
        },
        10000);
}

void GuiMadPageSystemDetail::populate(const rapidjson::Value& result)
{
    const std::string backendLabel {MadJson::getString(result, "backend_label")};
    const bool managed {MadJson::getBool(result, "managed")};
    const std::string artPath {MadJson::getString(result, "art")};
    const rapidjson::Value& toggles {MadJson::getMember(result, "toggles")};
    const bool hasToggles {toggles.IsArray() && toggles.Size() > 0};

    float contentY {mViewportPos.y};

    const float artHeight {mViewportSize.y * 0.24f};
    if (!artPath.empty()) {
        mArt = std::make_shared<ImageComponent>();
        mArt->setOrigin(0.5f, 0.5f);
        mArt->setMaxSize(mViewportSize.x * 0.4f, artHeight);
        mArt->setImage(artPath);
        mArt->setPosition(mViewportPos.x + mViewportSize.x / 2.0f, contentY + artHeight / 2.0f);
        addChild(mArt.get());
        contentY += artHeight + mViewportSize.y * 0.02f;
    }

    // The backend line is always shown; "not router-managed" is an independent
    // second notice whenever managed is false (matches the Tk reference).
    mBackendLine = std::make_shared<TextComponent>(
        "backend = " + backendLabel, Font::get(FONT_SIZE_SMALL), mMenuColorSecondary,
        ALIGN_CENTER, ALIGN_CENTER, glm::ivec2 {0, 0});
    mBackendLine->setPosition(mViewportPos.x, contentY);
    mBackendLine->setSize(mViewportSize.x, Font::get(FONT_SIZE_SMALL)->getHeight());
    addChild(mBackendLine.get());
    contentY += mBackendLine->getSize().y;

    if (!managed) {
        mManagedLine = std::make_shared<TextComponent>(
            "input: not router-managed", Font::get(FONT_SIZE_SMALL), mMenuColorSecondary,
            ALIGN_CENTER, ALIGN_CENTER, glm::ivec2 {0, 0});
        mManagedLine->setPosition(mViewportPos.x, contentY);
        mManagedLine->setSize(mViewportSize.x, Font::get(FONT_SIZE_SMALL)->getHeight());
        addChild(mManagedLine.get());
        contentY += mManagedLine->getSize().y;
    }

    contentY += mViewportSize.y * 0.03f;

    if (!hasToggles)
        return;

    mList = std::make_shared<ComponentList>();
    mList->setPosition(mViewportPos.x, contentY);
    mList->setSize(mViewportSize.x, mViewportPos.y + mViewportSize.y - contentY);
    addChild(mList.get());

    for (rapidjson::SizeType i {0}; i < toggles.Size(); ++i) {
        const rapidjson::Value& toggle {toggles[i]};
        const std::string flag {MadJson::getString(toggle, "key")};
        const std::string label {MadJson::getString(toggle, "label", flag)};
        const bool value {MadJson::getBool(toggle, "value")};

        // Default-construct + setState (the upstream menu idiom): the
        // constructor stores the state but always renders the OFF graphic —
        // only setState syncs the image.
        auto switchComp = std::make_shared<SwitchComponent>();
        switchComp->setState(value);
        // Raw pointer: capturing the shared_ptr would store a self-owning
        // closure inside the component (reference cycle → leak). The row /
        // mToggles own the component for the page's lifetime, and the callback
        // only fires from the component's own input().
        SwitchComponent* sc {switchComp.get()};
        switchComp->setCallback([this, flag, sc] { setFlag(flag, sc->getState()); });

        ComponentListRow row;
        row.addElement(std::make_shared<TextComponent>(label, Font::get(FONT_SIZE_MEDIUM),
                                                       mMenuColorPrimary, ALIGN_LEFT, ALIGN_CENTER,
                                                       glm::ivec2 {0, 0}),
                       true);
        row.addElement(switchComp, false);
        mList->addRow(row);

        mToggles.push_back(ToggleRow {flag, switchComp});
    }

    mList->onFocusGained();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageSystemDetail::setFlag(const std::string& flag, const bool value)
{
    const std::string system {mSystem};
    pageRequest(
        "policy.set_system_flag",
        [system, flag, value](MadJson::Writer& writer) {
            writer.Key("system");
            writer.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
            writer.Key("flag");
            writer.String(flag.c_str(), static_cast<rapidjson::SizeType>(flag.length()));
            writer.Key("value");
            writer.Bool(value);
        },
        [this, flag, value](bool ok, const rapidjson::Value& payload) {
            std::shared_ptr<SwitchComponent> switchComp;
            for (ToggleRow& toggle : mToggles) {
                if (toggle.flag == flag)
                    switchComp = toggle.switchComp;
            }

            if (!ok) {
                // Roll the UI back to the state the backend still has.
                if (switchComp != nullptr)
                    switchComp->setState(!value);
                footer()->flash("Couldn't save " + mSystem + "." + flag + ": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }

            // result.merged is the on-disk truth; re-sync the switch from it. A flag
            // missing from the merged policy means the display default applies
            // (warn_* flags default to on, everything else to off).
            const rapidjson::Value& systems {
                MadJson::getMember(MadJson::getMember(payload, "merged"), "systems")};
            const rapidjson::Value& systemTable {MadJson::getMember(systems, mSystem.c_str())};
            const bool displayDefault {flag.rfind("warn_", 0) == 0};
            const bool actual {MadJson::getBool(systemTable, flag.c_str(), displayDefault)};
            if (switchComp != nullptr)
                switchComp->setState(actual);
            footer()->flash("Saved " + mSystem + "." + flag);
        });
}

bool GuiMadPageSystemDetail::input(InputConfig* config, Input input)
{
    if (mList != nullptr)
        return mList->input(config, input);
    return false;
}

void GuiMadPageSystemDetail::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->moveCursor(direction * 6);
}

std::vector<HelpPrompt> GuiMadPageSystemDetail::getHelpPrompts()
{
    if (mList != nullptr)
        return mList->getHelpPrompts();
    return std::vector<HelpPrompt>();
}

void GuiMadPageSystemDetail::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->getCursorId();
}

void GuiMadPageSystemDetail::onRestoreFocus()
{
    if (mList != nullptr)
        mList->moveCursor(mFocusCookie - mList->getCursorId());
}
