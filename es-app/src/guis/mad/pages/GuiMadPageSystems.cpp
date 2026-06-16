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
#include "guis/mad/MadTheme.h"

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
            if (mIntro != nullptr) {
                removeChild(mIntro.get());
                mIntro.reset();
            }

            // Clarify scope: this page is the controller-ROUTER's per-system policy.
            // It governs RetroArch games + the standalone emulators the router still
            // manages — NOT the ones configured under Standalones (Switch, …).
            mIntro = std::make_shared<TextComponent>(
                "Controller routing for RetroArch games + router-managed standalones "
                "(Dolphin, PS2, PS3, Xbox, Model 3). Switch and other migrated "
                "emulators are configured under Standalones, not here.",
                Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary),
                ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 1});
            mIntro->setPosition(mViewportPos.x, mViewportPos.y);
            mIntro->setSize(mViewportSize.x, 0.0f);
            addChild(mIntro.get());
            const float introH {mIntro->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};

            mGrid = std::make_shared<MadTileGrid>();
            mGrid->setPosition(mViewportPos.x, mViewportPos.y + introH);
            mGrid->setSize(mViewportSize.x, mViewportSize.y - introH);
            mGrid->setTiles(tiles);
            mGrid->setOnPick([this](const std::string& system) {
                mPanel->pushPage(new GuiMadPageSystemDetail(mPanel, system));
            });
            addChild(mGrid.get());
            mGrid->setCursorIndex(mFocusCookie);
            mGrid->onFocusGained(); // The grid is this page's only focusable.

            // A flash, not a sticky: a permanent status would cover the help
            // prompts (the footer owns the help row whenever it has text).
            footer()->flash(std::to_string(tiles.size()) +
                                " systems — ● = locally configured",
                            5000);
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
        "backend = " + backendLabel, Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary),
        ALIGN_CENTER, ALIGN_CENTER, glm::ivec2 {0, 0});
    mBackendLine->setPosition(mViewportPos.x, contentY);
    mBackendLine->setSize(mViewportSize.x, Font::get(FONT_SIZE_SMALL)->getHeight());
    addChild(mBackendLine.get());
    contentY += mBackendLine->getSize().y;

    if (!managed) {
        mManagedLine = std::make_shared<TextComponent>(
            "input: not router-managed", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary),
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
                                                       MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
                                                       glm::ivec2 {0, 0}),
                       true);
        row.addElement(switchComp, false);
        mList->addRow(row);

        mToggles.push_back(ToggleRow {flag, switchComp});
    }

    // RetroArch per-system options (present only for RA systems): a header row
    // then a SwitchComponent per option, persisted to the system's RA cfgs.
    const rapidjson::Value& raOptions {MadJson::getMember(result, "ra_options")};
    if (raOptions.IsArray() && raOptions.Size() > 0) {
        ComponentListRow header;
        header.addElement(std::make_shared<TextComponent>(
                              "RETROARCH OPTIONS", Font::get(FONT_SIZE_SMALL),
                              MadTheme::color(MadColor::Title), ALIGN_LEFT, ALIGN_CENTER,
                              glm::ivec2 {0, 0}),
                          true);
        mList->addRow(header);
        for (rapidjson::SizeType i {0}; i < raOptions.Size(); ++i) {
            const rapidjson::Value& opt {raOptions[i]};
            const std::string id {MadJson::getString(opt, "id")};
            const std::string label {MadJson::getString(opt, "label", id)};
            const bool value {MadJson::getBool(opt, "value")};
            auto switchComp = std::make_shared<SwitchComponent>();
            switchComp->setState(value);
            SwitchComponent* sc {switchComp.get()};
            switchComp->setCallback([this, id, sc] { setRaOption(id, sc->getState()); });
            ComponentListRow row;
            row.addElement(std::make_shared<TextComponent>(label, Font::get(FONT_SIZE_MEDIUM),
                                                           MadTheme::color(MadColor::Primary), ALIGN_LEFT,
                                                           ALIGN_CENTER, glm::ivec2 {0, 0}),
                           true);
            row.addElement(switchComp, false);
            mList->addRow(row);
            mRaToggles.push_back(ToggleRow {id, switchComp});
        }
    }

    mList->onFocusGained();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageSystemDetail::applyToggle(
    std::vector<ToggleRow>& rows, const std::string& method, const std::string& keyField,
    const std::string& key, bool value, const std::string& errorLabel,
    std::function<bool(const rapidjson::Value&)> resolveActual,
    std::function<void(bool)> onSuccess)
{
    const std::string system {mSystem};
    // Pointer (not a captured reference-to-parameter): the response callback runs
    // async, and `rows` is one of this->mToggles/mRaToggles, so the member address
    // is valid for as long as `this` is (pageRequest drops the callback if dead).
    std::vector<ToggleRow>* rowsPtr {&rows};
    pageRequest(
        method,
        [system, keyField, key, value](MadJson::Writer& writer) {
            writer.Key("system");
            writer.String(system.c_str(), static_cast<rapidjson::SizeType>(system.length()));
            writer.Key(keyField.c_str(), static_cast<rapidjson::SizeType>(keyField.length()));
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("value");
            writer.Bool(value);
        },
        [this, rowsPtr, key, value, errorLabel, resolveActual, onSuccess](
            bool ok, const rapidjson::Value& payload) {
            std::shared_ptr<SwitchComponent> switchComp;
            for (ToggleRow& row : *rowsPtr) {
                if (row.key == key)
                    switchComp = row.switchComp;
            }
            if (!ok) {
                // Roll the UI back to the state the backend still has.
                if (switchComp != nullptr)
                    switchComp->setState(!value);
                footer()->flash(errorLabel + ": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            // The backend's authoritative value (re-sync shape differs per RPC).
            const bool actual {resolveActual(payload)};
            if (switchComp != nullptr)
                switchComp->setState(actual);
            onSuccess(actual);
        });
}

void GuiMadPageSystemDetail::setFlag(const std::string& flag, const bool value)
{
    const std::string system {mSystem};
    applyToggle(
        mToggles, "policy.set_system_flag", "flag", flag, value,
        "Couldn't save " + system + "." + flag,
        [this, flag](const rapidjson::Value& payload) {
            // result.merged is the on-disk truth; a flag missing from the merged
            // policy means the display default applies (warn_* default on, else off).
            const rapidjson::Value& systems {
                MadJson::getMember(MadJson::getMember(payload, "merged"), "systems")};
            const rapidjson::Value& systemTable {MadJson::getMember(systems, mSystem.c_str())};
            const bool displayDefault {flag.rfind("warn_", 0) == 0};
            return MadJson::getBool(systemTable, flag.c_str(), displayDefault);
        },
        [this, system, flag](bool) { footer()->flash("Saved " + system + "." + flag); });
}

void GuiMadPageSystemDetail::setRaOption(const std::string& id, const bool value)
{
    applyToggle(
        mRaToggles, "systems.set_ra_option", "id", id, value, "Couldn't set " + id,
        [value](const rapidjson::Value& payload) {
            return MadJson::getBool(payload, "value", value);  // flat payload.value
        },
        [this, id](bool actual) {
            footer()->flash(actual ? "Enabled " + id : "Disabled " + id);
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
