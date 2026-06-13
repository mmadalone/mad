//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageSplash.cpp
//
//  MAD control panel: Splash section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageSplash.h"

#include "components/SwitchComponent.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

GuiMadPageSplash::GuiMadPageSplash(GuiMadPanel* panel)
    : MadPage {panel, "STARTUP SPLASH"}
    , mPickerCap {200}
{
}

void GuiMadPageSplash::build()
{
    setLoadingText("Loading splash settings…");

    pageRequest("splash.get", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        if (!ok) {
            setLoadingText("");
            footer()->setStatus("Couldn't load splash settings: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }

        // modes/fits arrive as [value, label] pairs.
        auto parseOptions = [&payload](const char* key) {
            std::vector<Option> options;
            const rapidjson::Value& list {MadJson::getMember(payload, key)};
            if (list.IsArray()) {
                for (rapidjson::SizeType i {0}; i < list.Size(); ++i) {
                    const rapidjson::Value& pair {list[i]};
                    if (pair.IsArray() && pair.Size() >= 2 && pair[0].IsString() &&
                        pair[1].IsString())
                        options.push_back(Option {pair[0].GetString(), pair[1].GetString()});
                }
            }
            return options;
        };
        mModes = parseOptions("modes");
        mFits = parseOptions("fits");
        mPickerCap = MadJson::getInt(payload, "picker_cap", 200);
        applyConfig(MadJson::getMember(payload, "splash"));

        pageRequest("splash.images", nullptr, [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            mImages.clear();
            if (ok) {
                const rapidjson::Value& images {MadJson::getMember(payload, "images")};
                if (images.IsArray()) {
                    for (rapidjson::SizeType i {0}; i < images.Size(); ++i) {
                        if (images[i].IsString())
                            mImages.emplace_back(images[i].GetString());
                    }
                }
            }
            // A flash, not a sticky: a permanent status would cover the help
            // prompts (the footer owns the help row whenever it has text).
            footer()->flash("Splash images live in ~/ES-DE/splashscreens (png/jpg/svg)",
                            5000);
            rebuildList(0);
        });
    });
}

void GuiMadPageSplash::applyConfig(const rapidjson::Value& splash)
{
    mMode = MadJson::getString(splash, "mode", "off");
    mFit = MadJson::getString(splash, "fit", "contain");
    mImage = MadJson::getString(splash, "image", "");
    mPool.clear();
    const rapidjson::Value& images {MadJson::getMember(splash, "images")};
    if (images.IsArray()) {
        for (rapidjson::SizeType i {0}; i < images.Size(); ++i) {
            if (images[i].IsString())
                mPool.emplace(images[i].GetString());
        }
    }
}

void GuiMadPageSplash::rebuildList(const int cursorTo)
{
    if (mList != nullptr) {
        removeChild(mList.get());
        mList.reset();
    }
    if (mCaption != nullptr) {
        removeChild(mCaption.get());
        mCaption.reset();
    }

    std::string captionText;
    if (mMode == "fixed_image") {
        captionText =
            static_cast<int>(mImages.size()) <= mPickerCap ?
                std::to_string(mImages.size()) + " images in ~/ES-DE/splashscreens" :
                "showing first " + std::to_string(mPickerCap) + " of " +
                    std::to_string(mImages.size()) +
                    " — for others set [esde_splash].image in the config";
    }
    else if (mMode == "random_image") {
        if (static_cast<int>(mImages.size()) <= mPickerCap) {
            captionText = "Tick which images the random splash may pick — none ticked = all " +
                          std::to_string(mImages.size());
        }
        else if (!mPool.empty()) {
            // The generator honours a previously saved [esde_splash].images
            // subset even when the picker is over cap — don't claim otherwise.
            captionText = std::to_string(mImages.size()) +
                          " images, but a saved pool of " + std::to_string(mPool.size()) +
                          " is active — random only picks from those " +
                          std::to_string(mPool.size());
        }
        else {
            captionText = "Pool has " + std::to_string(mImages.size()) +
                          " images — random already uses ALL of them; to curate, keep fewer "
                          "files in ~/ES-DE/splashscreens";
        }
    }

    // {0,1} + setSize(width, 0): wrap to the viewport width and auto-expand
    // vertically so long captions (e.g. the random-pool note) are fully
    // readable instead of clipping at a fixed 1.3-line height.
    mCaption = std::make_shared<TextComponent>(captionText, Font::get(FONT_SIZE_SMALL),
                                               MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER,
                                               glm::ivec2 {0, 1});
    mCaption->setPosition(mViewportPos.x, mViewportPos.y);
    mCaption->setSize(mViewportSize.x, 0.0f);
    addChild(mCaption.get());

    // The list starts below the ACTUAL wrapped caption height (+ a small gap).
    const float captionHeight {mCaption->getSize().y +
                               Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f};
    mList = std::make_shared<ComponentList>();
    mList->setPosition(mViewportPos.x, mViewportPos.y + captionHeight);
    mList->setSize(mViewportSize.x, mViewportSize.y - captionHeight);
    addChild(mList.get());

    addCycleRow("MODE", "mode", mModes, mMode);

    if (mMode != "off")
        addCycleRow("FIT", "fit", mFits, mFit);

    if (mMode == "fixed_image") {
        const int shown {std::min(static_cast<int>(mImages.size()), mPickerCap)};
        for (int i {0}; i < shown; ++i) {
            const std::string name {mImages[i]};
            const bool selected {name == mImage};
            ComponentListRow row;
            auto text = std::make_shared<TextComponent>(selected ? "● " + name : name,
                                                        Font::get(FONT_SIZE_MEDIUM),
                                                        MadTheme::color(MadColor::Primary), ALIGN_LEFT,
                                                        ALIGN_CENTER, glm::ivec2 {0, 0});
            if (selected)
                text->setColor(MadTheme::color(MadColor::Green));
            row.addElement(text, true);
            const int rowIndex {static_cast<int>(mList->size())};
            row.makeAcceptInputHandler(
                [this, name, rowIndex] { setSplash("image", name, rowIndex); });
            mList->addRow(row);
        }
    }
    else if (mMode == "random_image" && static_cast<int>(mImages.size()) <= mPickerCap) {
        for (const std::string& name : mImages) {
            // Default-construct + setState (the upstream menu idiom): the
            // constructor stores the state but always renders the OFF graphic —
            // only setState syncs the image.
            auto switchComp = std::make_shared<SwitchComponent>();
            switchComp->setState(mPool.count(name) > 0);
            // Raw pointer: capturing the shared_ptr would store a self-owning
            // closure inside the component (reference cycle → leak). The row
            // owns the component for the page's lifetime, and the callback only
            // fires from the component's own input().
            SwitchComponent* sc {switchComp.get()};
            switchComp->setCallback([this, name, sc] {
                const bool on {sc->getState()};
                pageRequest(
                    "splash.toggle_image",
                    [name, on](MadJson::Writer& writer) {
                        writer.Key("name");
                        writer.String(name.c_str(),
                                      static_cast<rapidjson::SizeType>(name.length()));
                        writer.Key("on");
                        writer.Bool(on);
                    },
                    [this, name, on, sc](bool ok, const rapidjson::Value& payload) {
                        if (!ok) {
                            sc->setState(!on);
                            footer()->flash(
                                "Couldn't save the pool: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                            return;
                        }
                        applyConfig(MadJson::getMember(payload, "splash"));
                        sc->setState(mPool.count(name) > 0);
                        footer()->flash(std::string {"Saved splash pool ("} +
                                        (mPool.empty() ? "all images" :
                                                         std::to_string(mPool.size()) +
                                                             " selected") +
                                        ")");
                    });
            });

            ComponentListRow row;
            row.addElement(std::make_shared<TextComponent>(name, Font::get(FONT_SIZE_MEDIUM),
                                                           MadTheme::color(MadColor::Primary), ALIGN_LEFT,
                                                           ALIGN_CENTER, glm::ivec2 {0, 0}),
                           true);
            row.addElement(switchComp, false);
            mList->addRow(row);
        }
    }
    else if (mMode == "random_image" && !mPool.empty()) {
        // Over the picker cap with a saved subset active: offer a one-shot
        // reset back to use-all (the per-image toggles aren't shown here).
        ComponentListRow row;
        row.addElement(std::make_shared<TextComponent>("CLEAR SAVED POOL (USE ALL IMAGES)",
                                                       Font::get(FONT_SIZE_MEDIUM),
                                                       MadTheme::color(MadColor::Primary), ALIGN_LEFT,
                                                       ALIGN_CENTER, glm::ivec2 {0, 0}),
                       true);
        row.makeAcceptInputHandler([this] { clearSavedPool(); });
        mList->addRow(row);
    }

    if (cursorTo > 0)
        mList->moveCursor(cursorTo);
    mList->onFocusGained();
    mPanel->refreshHelpPrompts();
}

void GuiMadPageSplash::addCycleRow(const std::string& label,
                                   const std::string& key,
                                   const std::vector<Option>& options,
                                   const std::string& current)
{
    ComponentListRow row;
    row.addElement(std::make_shared<TextComponent>(label, Font::get(FONT_SIZE_MEDIUM),
                                                   MadTheme::color(MadColor::Primary), ALIGN_LEFT, ALIGN_CENTER,
                                                   glm::ivec2 {0, 0}),
                   true);

    auto value = std::make_shared<TextComponent>(optionLabel(options, current),
                                                 Font::get(FONT_SIZE_MEDIUM, FONT_PATH_LIGHT),
                                                 MadTheme::color(MadColor::Primary), ALIGN_RIGHT, ALIGN_CENTER,
                                                 glm::ivec2 {0, 0});
    value->setSize(mViewportSize.x * 0.55f, value->getFont()->getHeight());
    row.addElement(value, false);

    // The handler reads the LIVE value via cycleOption() — capturing `current`
    // would make rapid presses recompute from a stale snapshot.
    row.inputHandler = [this, key, options](InputConfig* config, Input input) {
        if (input.value == 0)
            return false;
        if (config->isMappedLike("left", input)) {
            cycleOption(key, options, -1);
            return true;
        }
        if (config->isMappedLike("right", input) || config->isMappedTo("a", input)) {
            cycleOption(key, options, 1);
            return true;
        }
        return false;
    };

    mList->addRow(row);
}

void GuiMadPageSplash::cycleOption(const std::string& key,
                                   const std::vector<Option>& options,
                                   const int direction)
{
    if (options.empty())
        return;
    // Cycle from the live member so burst presses advance step by step instead
    // of collapsing onto the same next value and sending duplicate writes.
    std::string& current {key == "fit" ? mFit : mMode};
    int index {0};
    for (size_t i {0}; i < options.size(); ++i) {
        if (options[i].value == current)
            index = static_cast<int>(i);
    }
    const int count {static_cast<int>(options.size())};
    const int next {(index + direction + count) % count};
    // Optimistic update; the ack re-syncs from the on-disk truth (applyConfig).
    current = options[next].value;
    setSplash(key, options[next].value, key == "fit" ? 1 : 0);
}

void GuiMadPageSplash::setSplash(const std::string& key,
                                 const std::string& value,
                                 const int cursorTo)
{
    pageRequest(
        "splash.set",
        [key, value](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("value");
            writer.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
        },
        [this, key, cursorTo](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save splash." + key + ": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            applyConfig(MadJson::getMember(payload, "splash"));
            footer()->flash("Saved splash." + key);
            // The row set depends on the mode, so rebuild from the fresh config.
            rebuildList(cursorTo);
        });
}

void GuiMadPageSplash::clearSavedPool()
{
    pageRequest(
        "splash.set",
        [](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String("images");
            writer.Key("value");
            // splash.set writes the raw value; an empty list = use-all semantics.
            writer.StartArray();
            writer.EndArray();
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't clear the saved pool: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            applyConfig(MadJson::getMember(payload, "splash"));
            footer()->flash("Cleared the saved pool — random now uses all images");
            rebuildList(0);
        });
}

std::string GuiMadPageSplash::optionLabel(const std::vector<Option>& options,
                                          const std::string& value) const
{
    for (const Option& option : options) {
        if (option.value == value)
            return option.label;
    }
    return value;
}

bool GuiMadPageSplash::input(InputConfig* config, Input input)
{
    if (mList != nullptr)
        return mList->input(config, input);
    return false;
}

void GuiMadPageSplash::pageScroll(int direction)
{
    if (mList != nullptr)
        mList->moveCursor(direction * 6);
}

std::vector<HelpPrompt> GuiMadPageSplash::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mList != nullptr)
        prompts = mList->getHelpPrompts();
    prompts.push_back(HelpPrompt("a", "select"));
    return prompts;
}

void GuiMadPageSplash::onSaveFocus()
{
    if (mList != nullptr)
        mFocusCookie = mList->getCursorId();
}

void GuiMadPageSplash::onRestoreFocus()
{
    if (mList != nullptr)
        mList->moveCursor(mFocusCookie - mList->getCursorId());
}
