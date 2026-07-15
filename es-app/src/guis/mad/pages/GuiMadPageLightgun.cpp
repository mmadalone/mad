//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLightgun.cpp
//
//  MAD control panel: Lightgun / Sinden section (deck-patches).
//

#include "guis/mad/pages/GuiMadPageLightgun.h"

#include "Sound.h"
#include "Window.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadMsgBox.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (generic picker).

#include <SDL2/SDL_keyboard.h>

#include <cmath>
#include <cstdio>
#include <sys/stat.h>
#include "guis/mad/MadTheme.h"

//  ── MadLightgunPageBase (shared control-column scaffolding) ──

MadLightgunPageBase::MadLightgunPageBase(GuiMadPanel* panel, const std::string& title)
    : MadPage {panel, title}
    , mY {0.0f}
    , mFocus {0}
    , mFocusCookie2 {0}
    , mNextRow {0}
    , mScrollCookie {0.0f}
    , mBuilt {false}
{
}

void MadLightgunPageBase::clearColumn()
{
    // Children first (dtors self-detach from the live scroll view), THEN the
    // scroll view — removeChild on the wrong parent would dangle.
    mControls.clear();
    mWidgets.clear();
    if (mScroll != nullptr) {
        removeChild(mScroll.get());
        mScroll.reset();
    }
}

void MadLightgunPageBase::beginColumn()
{
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
    if (mBuilt)
        mFocusCookie2 = mFocus;
    clearColumn();
    mScroll = std::make_shared<MadScrollView>();
    mScroll->setPosition(mViewportPos.x, mViewportPos.y);
    mScroll->setSize(mViewportSize.x, mViewportSize.y);
    addChild(mScroll.get());
    mY = 0.0f;
    mNextRow = 0;
}

void MadLightgunPageBase::endColumn()
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mScroll->setContentHeight(mY + smallHeight * 0.5f);
    mScroll->setScrollOffset(mScrollCookie);
    mBuilt = true;
    if (!mControls.empty()) {
        setFocus(glm::clamp(mFocusCookie2, 0, static_cast<int>(mControls.size()) - 1));
        followFocus();
    }
    mPanel->refreshHelpPrompts();
}

std::shared_ptr<TextComponent> MadLightgunPageBase::addBlock(const std::string& text,
                                                              const float fontSize,
                                                              const unsigned int color,
                                                              const float padAfter)
{
    auto component = std::make_shared<TextComponent>(text, Font::get(fontSize), color,
                                                     ALIGN_LEFT, ALIGN_CENTER,
                                                     glm::ivec2 {0, 1});
    component->setPosition(0.0f, mY);
    component->setSize(mScroll->getSize().x, 0.0f); // Autosize within the column.
    mScroll->addChild(component.get());
    mWidgets.emplace_back(component);
    mY += component->getSize().y + padAfter;
    return component;
}

void MadLightgunPageBase::header(const std::string& label)
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    mY += smallHeight * 0.45f;
    addBlock(label, FONT_SIZE_SMALL, MadTheme::color(MadColor::Title), smallHeight * 0.15f);
}

void MadLightgunPageBase::caption(const std::string& help)
{
    if (!help.empty())
        addBlock("    " + help, FONT_SIZE_MINI, MadTheme::color(MadColor::Secondary),
                 Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);
}

std::shared_ptr<MadChipRow> MadLightgunPageBase::addChips(
    const std::vector<MadChipRow::Chip>& chips, const bool momentary)
{
    auto row = std::make_shared<MadChipRow>();
    row->setMomentary(momentary);
    row->setPosition(0.0f, mY);
    row->setSize(mScroll->getSize().x, 1.0f);
    row->setChips(chips);
    row->setSize(mScroll->getSize().x, std::max(1.0f, row->contentHeight()));
    mScroll->addChild(row.get());
    mWidgets.emplace_back(row);
    mControls.push_back(
        {Control::Type::Chips, row.get(), mY, mY + row->getSize().y, mNextRow++});
    mY += row->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return row;
}

std::shared_ptr<MadStepper> MadLightgunPageBase::addStepper(
    const std::string& label, const float lo, const float hi, const float step,
    const std::function<std::string(float)>& format,
    const std::function<void(float)>& onChange, const float initial,
    const float widthFraction, const float valueWidthFraction)
{
    auto stepper = std::make_shared<MadStepper>(label, lo, hi, step, format, onChange);
    stepper->setPosition(0.0f, mY);
    stepper->setSize(mScroll->getSize().x * widthFraction,
                     Font::get(FONT_SIZE_MEDIUM)->getHeight() * 1.4f);
    stepper->setValueWidthFraction(valueWidthFraction);
    stepper->setValue(initial);
    mScroll->addChild(stepper.get());
    mWidgets.emplace_back(stepper);
    mControls.push_back({Control::Type::Stepper, stepper.get(), mY,
                         mY + stepper->getSize().y, mNextRow++});
    mY += stepper->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return stepper;
}

std::shared_ptr<ButtonComponent> MadLightgunPageBase::addButton(
    const std::string& text, const std::function<void()>& callback)
{
    auto button = std::make_shared<ButtonComponent>(text, text, callback);
    button->setPosition(0.0f, mY);
    mScroll->addChild(button.get());
    mWidgets.emplace_back(button);
    mControls.push_back({Control::Type::Button, button.get(), mY,
                         mY + button->getSize().y, mNextRow++});
    mY += button->getSize().y + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return button;
}

std::vector<std::shared_ptr<ButtonComponent>> MadLightgunPageBase::addButtonRow(
    const std::vector<std::pair<std::string, std::function<void()>>>& items,
    const bool upperCase)
{
    std::vector<std::shared_ptr<ButtonComponent>> buttons;
    if (items.empty())
        return buttons;
    const float gap {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};
    int rowId {mNextRow++};
    float x {0.0f};
    float lineHeight {0.0f};
    for (const auto& item : items) {
        auto button = std::make_shared<ButtonComponent>(item.first, item.first, item.second);
        if (!upperCase)
            button->setText(item.first, item.first, false);
        if (x > 0.0f && x + button->getSize().x > mScroll->getSize().x) {
            x = 0.0f; // Wrap onto the next line — a NEW focus row, so up/down moves
            mY += lineHeight + gap * 0.4f; // between lines (true 4-way) instead of
            lineHeight = 0.0f;             // walking the whole row with right.
            rowId = mNextRow++;
        }
        button->setPosition(x, mY);
        mScroll->addChild(button.get());
        mWidgets.emplace_back(button);
        mControls.push_back({Control::Type::Button, button.get(), mY,
                             mY + button->getSize().y, rowId});
        x += button->getSize().x + gap;
        lineHeight = std::max(lineHeight, button->getSize().y);
        buttons.emplace_back(button);
    }
    mY += lineHeight + Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f;
    return buttons;
}

void MadLightgunPageBase::reflowRow(const int row)
{
    const float gap {Font::get(FONT_SIZE_SMALL)->getHeight() * 0.5f};
    float x {0.0f};
    float lastY {-1.0f};
    for (Control& control : mControls) {
        if (control.row != row)
            continue;
        const float y {control.comp->getPosition().y};
        if (lastY >= 0.0f && y != lastY)
            x = 0.0f; // Next wrapped line of the same focus row.
        lastY = y;
        control.comp->setPosition(x, y);
        x += control.comp->getSize().x + gap;
    }
}

void MadLightgunPageBase::moveControls(const size_t fromIndex, const float deltaY)
{
    for (size_t i {fromIndex}; i < mControls.size(); ++i) {
        Control& control {mControls[i]};
        control.comp->setPosition(control.comp->getPosition().x,
                                  control.comp->getPosition().y + deltaY);
        control.top += deltaY;
        control.bottom += deltaY;
    }
}

int MadLightgunPageBase::firstOfRow(const int row) const
{
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (mControls[i].row == row)
            return static_cast<int>(i);
    }
    return -1;
}

int MadLightgunPageBase::nearestOfRow(const int row, const float centerX) const
{
    int best {-1};
    float bestDist {0.0f};
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (mControls[i].row != row)
            continue;
        const float cx {mControls[i].comp->getPosition().x +
                        mControls[i].comp->getSize().x * 0.5f};
        const float d {std::fabs(cx - centerX)};
        if (best < 0 || d < bestDist) {
            best = static_cast<int>(i);
            bestDist = d;
        }
    }
    return best;
}

void MadLightgunPageBase::setFocus(const int index)
{
    if (mControls.empty())
        return;
    mFocus = glm::clamp(index, 0, static_cast<int>(mControls.size()) - 1);
    for (size_t i {0}; i < mControls.size(); ++i) {
        if (static_cast<int>(i) == mFocus)
            mControls[i].comp->onFocusGained();
        else
            mControls[i].comp->onFocusLost();
    }
    mPanel->refreshHelpPrompts();
}

void MadLightgunPageBase::followFocus()
{
    if (mScroll == nullptr || mControls.empty())
        return;
    const Control& control {mControls[mFocus]};
    mScroll->ensureVisible(mFocus == 0 ? 0.0f : control.top, control.bottom);
}

bool MadLightgunPageBase::input(InputConfig* config, Input input)
{
    if (!mBuilt || mControls.empty())
        return false;
    if (mControls[mFocus].comp->input(config, input)) {
        followFocus();
        return true;
    }
    if (input.value == 0)
        return false;
    const int row {mControls[mFocus].row};
    const float curX {mControls[mFocus].comp->getPosition().x +
                      mControls[mFocus].comp->getSize().x * 0.5f};
    if (config->isMappedLike("up", input)) {
        int target {nearestOfRow(row - 1, curX)};       // column-aware: same x on line above
        if (target < 0) {                                // at the top row -> wrap to the last row
            int maxRow {0};
            for (const auto& c : mControls)
                maxRow = std::max(maxRow, c.row);
            target = nearestOfRow(maxRow, curX);
        }
        if (target >= 0 && target != mFocus) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(target);
            followFocus();
        }
        return true;
    }
    if (config->isMappedLike("down", input)) {
        int target {nearestOfRow(row + 1, curX)};        // column-aware: same x on line below
        if (target < 0)                                  // at the bottom row -> wrap to the first row
            target = nearestOfRow(0, curX);
        if (target >= 0 && target != mFocus) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(target);
            followFocus();
        }
        return true;
    }
    // Left/right walk a multi-button row (chips/steppers consume these
    // themselves before we get here).
    if (config->isMappedLike("left", input)) {
        if (mFocus > 0 && mControls[mFocus - 1].row == row) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(mFocus - 1);
            followFocus();
        }
        return true;
    }
    if (config->isMappedLike("right", input)) {
        if (mFocus < static_cast<int>(mControls.size()) - 1 &&
            mControls[mFocus + 1].row == row) {
            NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
            setFocus(mFocus + 1);
            followFocus();
        }
        return true;
    }
    return false;
}

void MadLightgunPageBase::pageScroll(int direction)
{
    if (!mBuilt || mScroll == nullptr || mControls.empty())
        return;
    std::vector<PagedTarget> targets;
    for (size_t i {0}; i < mControls.size(); ++i)
        targets.push_back({static_cast<int>(i), -1, mControls[i].top, mControls[i].bottom});
    bool moved {false};
    if (mScroll->overflows())
        moved = mScroll->pageScroll(direction);
    const float viewTop {mScroll->overflows() ? mScroll->scrollOffset() : 0.0f};
    const float viewBottom {viewTop + (mScroll->overflows() ? mScroll->getSize().y :
                                                              mScroll->contentHeight())};
    const int pick {pickPagedTarget(targets, direction, viewTop, viewBottom)};
    if (pick >= 0) {
        if (targets[pick].id != mFocus) {
            setFocus(targets[pick].id);
            moved = true;
        }
        followFocus();
    }
    if (moved)
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
}

std::vector<HelpPrompt> MadLightgunPageBase::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (!mBuilt || mControls.empty())
        return prompts;
    prompts.push_back(HelpPrompt("up/down", "choose"));
    switch (mControls[mFocus].type) {
        case Control::Type::Chips: {
            prompts.push_back(HelpPrompt("left/right", "choose"));
            prompts.push_back(HelpPrompt("a", "toggle"));
            break;
        }
        case Control::Type::Stepper: {
            prompts.push_back(HelpPrompt("left/right", "adjust"));
            break;
        }
        case Control::Type::Button: {
            const int row {mControls[mFocus].row};
            const bool multi {(mFocus > 0 && mControls[mFocus - 1].row == row) ||
                              (mFocus < static_cast<int>(mControls.size()) - 1 &&
                               mControls[mFocus + 1].row == row)};
            if (multi)
                prompts.push_back(HelpPrompt("left/right", "choose"));
            prompts.push_back(HelpPrompt("a", "select"));
            break;
        }
    }
    if (mScroll != nullptr && mScroll->overflows())
        prompts.push_back(HelpPrompt("ltrt", "scroll"));
    return prompts;
}

void MadLightgunPageBase::update(int deltaTime)
{
    if (mDeferred) {
        // Run OUTSIDE any widget's input frame (the relayout destroys it).
        const std::function<void()> deferred {std::move(mDeferred)};
        mDeferred = nullptr;
        deferred();
    }
    MadPage::update(deltaTime);
}

void MadLightgunPageBase::onSaveFocus()
{
    mFocusCookie2 = mFocus;
    if (mScroll != nullptr)
        mScrollCookie = mScroll->scrollOffset();
}

void MadLightgunPageBase::onRestoreFocus()
{
    if (!mBuilt)
        return;
    setFocus(mFocusCookie2);
    if (mScroll != nullptr)
        mScroll->setScrollOffset(mScrollCookie);
    followFocus();
}

//  ── GuiMadPageLightgun (root) ──

GuiMadPageLightgun::GuiMadPageLightgun(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "LIGHTGUN"}
    , mAlpha {0.12f}
    , mDeadzone {1.6f}
    , mSnap {1000}
{
}

GuiMadPageLightgun::~GuiMadPageLightgun()
{
    // The install keeps running (RunFullStream); just detach our callback.
    if (!mInstallToken.empty())
        backend()->clearStreamCallback(mInstallToken);
}

void GuiMadPageLightgun::build()
{
    setLoadingText("Loading lightgun state…");
    // Health first (fast), then the slow status — rebuild() needs both.
    pageRequest("sinden.health", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        if (ok) {
            mHealthDriver = MadJson::getBool(payload, "driver", true);
            mHealthMono = MadJson::getBool(payload, "mono", true);
        }
        pageRequest("sinden.status", nullptr,
                    [this](bool ok2, const rapidjson::Value& payload2) {
                        setLoadingText("");
                        if (!ok2) {
                            footer()->setStatus(
                                "Couldn't read the Sinden state: " +
                                    MadJson::getString(payload2, "message", "unknown error"),
                                true);
                            return;
                        }
                        rebuild(payload2);
                    });
    });
}

void GuiMadPageLightgun::installDriver()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new MadMsgBox(
        "Download and install the official Sinden driver (~25 MB) from "
        "sindenlightgun.com into ~/Lightgun? Your tuned LightgunMono.exe.config "
        "is kept.",
        "INSTALL",
        [this, alive] {
            if (alive.expired())
                return;
            pageRequest(
                "sinden.install", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (!ok) {
                        // No setStatus("") here: an EBUSY from a double-press
                        // must not wipe the LIVE install's footer sticky —
                        // the flash overlays it and the sticky comes back.
                        footer()->flash("Couldn't start the installer: " +
                                            MadJson::getString(payload, "message",
                                                               "unknown error"),
                                        5000, true);
                        return;
                    }
                    if (!mInstallToken.empty()) // Retry: drop the old callback.
                        backend()->clearStreamCallback(mInstallToken);
                    mInstallToken = MadJson::getString(payload, "stream");
                    footer()->setStatus("Installing the Sinden driver…");
                    backend()->setStreamCallback(
                        mInstallToken, [this, alive](const rapidjson::Value& data) {
                            if (alive.expired())
                                return;
                            if (MadJson::getBool(data, "done")) {
                                const int rc {MadJson::getInt(data, "rc", -1)};
                                footer()->setStatus("");
                                footer()->flash(rc == 0 ? "Sinden driver installed." :
                                                          "Install FAILED (exit " +
                                                              std::to_string(rc) + ").",
                                                6000, rc != 0);
                                // NO clearStreamCallback here — we are INSIDE
                                // that callback (erasing it would destroy the
                                // executing lambda). Tokens are never reused;
                                // the stale entry dies with the panel.
                                mInstallToken.clear();
                                if (rc == 0)
                                    build(); // Re-check health: banner goes away.
                                return;
                            }
                            const std::string line {MadJson::getString(data, "line")};
                            if (!line.empty())
                                footer()->setStatus(line);
                        });
                },
                15000);
        },
        "CANCEL", nullptr));
}

void GuiMadPageLightgun::applyDriverState(const bool running)
{
    if (mDriverLine == nullptr)
        return;
    mDriverLine->setText(running ? "●  Started" : "○  Stopped");
    mDriverLine->setColor(running ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Secondary));
}

void GuiMadPageLightgun::update(int deltaTime)
{
    // Keep the driver indicator live: Start/Stop are detached scripts that
    // take a few seconds — poll the daemon's pgrep state.
    mStatusPollAccum += deltaTime;
    if (mStatusPollAccum >= 2000 && mDriverLine != nullptr) {
        mStatusPollAccum = 0;
        pageRequest("sinden.status", nullptr,
                    [this](bool ok, const rapidjson::Value& payload) {
                        if (ok)
                            applyDriverState(MadJson::getBool(payload, "driver_running"));
                    });
    }
    MadLightgunPageBase::update(deltaTime);
}

void GuiMadPageLightgun::driverAction(const std::string& action)
{
    pageRequest(
        "sinden.driver",
        [action](MadJson::Writer& writer) {
            writer.Key("action");
            writer.String(action.c_str(), static_cast<rapidjson::SizeType>(action.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            footer()->setStatus("");
            footer()->flash(MadJson::getString(payload, "message", "unknown error"), 5000,
                            !ok);
        },
        10000);
}

void GuiMadPageLightgun::applySmoother()
{
    const float alpha {mAlpha};
    const float deadzone {mDeadzone};
    const int snap {mSnap};
    pageRequest(
        "sinden.smoother_set",
        [alpha, deadzone, snap](MadJson::Writer& writer) {
            writer.Key("alpha");
            writer.Double(static_cast<double>(alpha));
            writer.Key("deadzone");
            writer.Double(static_cast<double>(deadzone));
            writer.Key("snap");
            writer.Int(snap);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok)
                footer()->flash("Smoother: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
        });
}

void GuiMadPageLightgun::rebuild(const rapidjson::Value& result)
{
    const rapidjson::Value& smoother {MadJson::getMember(result, "smoother")};
    mAlpha = static_cast<float>(
        smoother.IsObject() && smoother.HasMember("alpha") && smoother["alpha"].IsNumber() ?
            smoother["alpha"].GetDouble() :
            0.12);
    mDeadzone = static_cast<float>(
        smoother.IsObject() && smoother.HasMember("deadzone") &&
                smoother["deadzone"].IsNumber() ?
            smoother["deadzone"].GetDouble() :
            1.6);
    mSnap = smoother.IsObject() && smoother.HasMember("snap") && smoother["snap"].IsNumber() ?
                static_cast<int>(smoother["snap"].GetDouble()) :
                1000;
    const bool smootherEnabled {MadJson::getBool(smoother, "enabled", true)};
    const bool ledEnabled {MadJson::getBool(result, "led_enabled")};
    const bool driverRunning {MadJson::getBool(result, "driver_running")};

    beginColumn();

    if (!mHealthDriver || !mHealthMono) {
        // Driver/mono missing: the install banner leads the page. INSTALL
        // downloads the OFFICIAL bundle from sindenlightgun.com (~25 MB) —
        // mono itself is pacman-owned (deck-post-update.sh reinstalls it).
        if (!mHealthDriver)
            addBlock("○  Sinden driver not installed (~/Lightgun is missing the driver "
                     "files).",
                     FONT_SIZE_SMALL, MadTheme::color(MadColor::Red),
                     Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f);
        if (!mHealthMono)
            addBlock("○  mono runtime missing — run deck-post-update.sh from Desktop "
                     "Mode (SteamOS updates wipe it).",
                     FONT_SIZE_SMALL, MadTheme::color(MadColor::Red),
                     Font::get(FONT_SIZE_SMALL)->getHeight() * 0.15f);
        if (!mHealthDriver)
            addButton("INSTALL DRIVER  (official download, ~25 MB)",
                      [this] { installDriver(); });
    }

    header("Driver");
    mDriverLine = addBlock(driverRunning ? "●  Started" : "○  Stopped", FONT_SIZE_SMALL,
                           driverRunning ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Secondary),
                           Font::get(FONT_SIZE_SMALL)->getHeight() * 0.2f);
    addButtonRow({{"START", [this] { driverAction("start"); }},
                  {"STOP", [this] { driverAction("stop"); }},
                  {"CALIBRATE GUNS", [this] { driverAction("calibrate"); }},
                  {"START BOTH GUNS", [this] { driverAction("test"); }}});

    header("Camera");
    addButton("CAMERA TUNING",
              [this] { mPanel->pushPage(new GuiMadPageLightgunCamera(mPanel)); });

    header("Buttons");
    addButtonRow(
        {{"P1 BUTTONS",
          [this] { mPanel->pushPage(new GuiMadPageLightgunButtons(mPanel, 1)); }},
         {"P2 BUTTONS",
          [this] { mPanel->pushPage(new GuiMadPageLightgunButtons(mPanel, 2)); }}});

    header("Recoil & gun behavior");
    addButtonRow(
        {{"P1 RECOIL & BEHAVIOR",
          [this] { mPanel->pushPage(new GuiMadPageLightgunBehavior(mPanel, 1)); }},
         {"P2 RECOIL & BEHAVIOR",
          [this] { mPanel->pushPage(new GuiMadPageLightgunBehavior(mPanel, 2)); }}});

    header("Pointer smoother");
    caption("More smoothing = steadier slow aim; less = snappier. Pick a preset, or "
            "fine-tune below (applies instantly).");
    // Presets are ACTIONS (momentary chips): Tk values, "Off" omits snap.
    auto presets = addChips({{"1.0 0.0", "Off", false},
                             {"0.30 0.8 500", "Snappy", false},
                             {"0.12 1.6 1000", "Default", false},
                             {"0.08 2.5 1200", "Smooth", false},
                             {"0.04 3.5 1500", "Heavy", false}},
                            true);
    presets->setOnToggle([this](const std::string& values, bool) {
        float alpha {0.0f}, deadzone {0.0f}, snap {-1.0f};
        if (std::sscanf(values.c_str(), "%f %f %f", &alpha, &deadzone, &snap) >= 2) {
            mAlpha = alpha;
            mDeadzone = deadzone;
            if (snap >= 0.0f)
                mSnap = static_cast<int>(snap); // "Off" omits snap → keep as-is.
            applySmoother();
            // Reflect the preset live in the sliders below (Tk parity).
            mAlphaStepper->setValue(mAlpha);
            mDeadzoneStepper->setValue(mDeadzone);
            mSnapStepper->setValue(static_cast<float>(mSnap));
        }
    });
    mAlphaStepper = addStepper(
        "alpha (left = smoother)", 0.04f, 1.0f, 0.01f,
        [](const float value) {
            char buffer[16];
            std::snprintf(buffer, sizeof(buffer), "%.2f", value);
            return std::string {buffer};
        },
        [this](const float value) {
            mAlpha = value;
            applySmoother();
        },
        glm::clamp(mAlpha, 0.04f, 1.0f));
    mDeadzoneStepper = addStepper(
        "deadzone (jitter)", 0.0f, 6.0f, 0.1f,
        [](const float value) {
            char buffer[16];
            std::snprintf(buffer, sizeof(buffer), "%.1f", value);
            return std::string {buffer};
        },
        [this](const float value) {
            mDeadzone = value;
            applySmoother();
        },
        glm::clamp(mDeadzone, 0.0f, 6.0f));
    mSnapStepper = addStepper(
        "snap threshold", 200.0f, 2000.0f, 50.0f,
        [](const float value) { return std::to_string(static_cast<int>(std::lround(value))); },
        [this](const float value) {
            mSnap = static_cast<int>(std::lround(value));
            applySmoother();
        },
        glm::clamp(static_cast<float>(mSnap), 200.0f, 2000.0f));
    auto smootherChip = addChips({{"smoother", "Cursor smoother", smootherEnabled}}, false);
    smootherChip->setOnToggle([this](const std::string&, bool) {
        // The canonical toggle script flips the marker; state re-reads on re-entry.
        pageRequest("sinden.smoother_toggle", nullptr,
                    [this](bool ok, const rapidjson::Value& payload) {
                        footer()->setStatus("");
                        footer()->flash(
                            MadJson::getString(payload, "message", "unknown error"), 4000,
                            !ok);
                    });
    });

    header("TV LED strip");
    auto ledChip = addChips({{"led", "LED strip on start/stop", ledEnabled}}, false);
    std::weak_ptr<MadChipRow> weakLed {ledChip};
    ledChip->setOnToggle([this, weakLed](const std::string&, const bool on) {
        pageRequest(
            "sinden.led_set",
            [on](MadJson::Writer& writer) {
                writer.Key("enabled");
                writer.Bool(on);
            },
            [this, weakLed, on](bool ok, const rapidjson::Value& payload) {
                if (!ok) {
                    if (auto chip = weakLed.lock())
                        chip->setChipState("led", !on); // Roll back.
                    footer()->flash(MadJson::getString(payload, "message", "unknown error"),
                                    4000, true);
                    return;
                }
                footer()->setStatus("");
                footer()->flash(MadJson::getString(payload, "message"), 4000);
            });
    });
    caption("Fires your Home-Assistant webhooks when the driver starts/stops. Base URL + "
            "webhook IDs live in sinden.conf (edit there).");

    endColumn();
}

//  ── GuiMadPageLightgunButtons ──

namespace
{
    // Sinden action code for an SDL keycode (mirrors the Tk keysym table:
    // 8-17 digits, 18-43 A-Z via Shift, 44-69 a-z, 70-80 specials, 82-93 F-keys).
    int sindenCodeForKey(const int sdlKey, const bool shift)
    {
        if (sdlKey >= SDLK_0 && sdlKey <= SDLK_9)
            return 8 + (sdlKey - SDLK_0);
        if (sdlKey >= SDLK_a && sdlKey <= SDLK_z)
            return (shift ? 18 : 44) + (sdlKey - SDLK_a);
        if (sdlKey >= SDLK_F1 && sdlKey <= SDLK_F12)
            return 82 + (sdlKey - SDLK_F1);
        switch (sdlKey) {
            case SDLK_RETURN: return 70;
            case SDLK_SPACE: return 71;
            case SDLK_ESCAPE: return 72;
            case SDLK_TAB: return 73;
            case SDLK_UP: return 74;
            case SDLK_DOWN: return 75;
            case SDLK_LEFT: return 76;
            case SDLK_RIGHT: return 77;
            case SDLK_PLUS: return 78;
            case SDLK_MINUS: return 79;
            case SDLK_PERIOD: return 80;
            default: return 0;
        }
    }

} // namespace

GuiMadPageLightgunButtons::GuiMadPageLightgunButtons(GuiMadPanel* panel, const int player)
    : MadLightgunPageBase {panel, "P" + std::to_string(player) + " BUTTONS (MOUSE MODE)"}
    , mPlayer {player}
    , mShowOff {false}
    , mShowMods {false}
    , mHaveData {false}
{
}

void GuiMadPageLightgunButtons::build()
{
    setLoadingText("Loading button map…");
    refresh();
}

void GuiMadPageLightgunButtons::onChildPopped()
{
    refresh(); // An action pick wrote the config; re-read truth.
}

void GuiMadPageLightgunButtons::refresh()
{
    const int player {mPlayer};
    pageRequest(
        "sinden.buttons",
        [player](MadJson::Writer& writer) {
            writer.Key("player");
            writer.Int(player);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't read the button map: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            mData.CopyFrom(payload, mData.GetAllocator());
            mHaveData = true;
            rebuild(mData);
        },
        10000);
}

void GuiMadPageLightgunButtons::rebuild(const rapidjson::Value& result)
{
    mRows.clear();
    mActionOptions.clear();
    mModOptions.clear();

    const rapidjson::Value& groups {MadJson::getMember(result, "groups")};
    if (groups.IsArray()) {
        for (rapidjson::SizeType i {0}; i < groups.Size(); ++i) {
            const std::string name {MadJson::getString(groups[i], "name")};
            const rapidjson::Value& options {MadJson::getMember(groups[i], "options")};
            if (!options.IsArray())
                continue;
            for (rapidjson::SizeType j {0}; j < options.Size(); ++j) {
                const int value {MadJson::getInt(options[j], "value")};
                mActionOptions.emplace_back(
                    std::to_string(value),
                    name + ":  " + MadJson::getString(options[j], "label"));
            }
        }
    }
    const rapidjson::Value& modifiers {MadJson::getMember(result, "modifiers")};
    if (modifiers.IsArray()) {
        for (rapidjson::SizeType i {0}; i < modifiers.Size(); ++i)
            mModOptions.emplace_back(std::to_string(MadJson::getInt(modifiers[i], "value")),
                                     MadJson::getString(modifiers[i], "label"));
    }

    const bool driverRunning {MadJson::getBool(result, "driver_running")};
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};

    beginColumn();
    addBlock("Remap each gun button. Picks save immediately; press Save to restart the "
             "driver so they take effect. Keyboard input never navigates this panel while "
             "this page is open.",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Primary), smallHeight * 0.2f);
    addBlock(driverRunning ?
                 "● dots light live as you press the gun's buttons (key-mapped actions; "
                 "mouse-mapped rows can't light here)." :
                 "Start the driver (run a Pew-Pew game, or Start it) to see the ● "
                 "live-press dots.",
             FONT_SIZE_MINI, driverRunning ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Secondary),
             smallHeight * 0.4f);

    auto toggles = addChips({{"off", "Show offscreen actions", mShowOff},
                             {"mods", "Modifiers (advanced)", mShowMods}},
                            false);
    toggles->setOnToggle([this](const std::string& which, const bool on) {
        if (which == "off")
            mShowOff = on;
        else
            mShowMods = on;
        // Deferred: a synchronous rebuild would destroy this chip row inside
        // its own input frame.
        deferRelayout([this] {
            if (mHaveData)
                rebuild(mData);
        });
    });

    const rapidjson::Value& rows {MadJson::getMember(result, "rows")};
    if (rows.IsArray()) {
        for (rapidjson::SizeType i {0}; i < rows.Size(); ++i) {
            const rapidjson::Value& rowData {rows[i]};
            Row row;
            row.base = MadJson::getString(rowData, "base");
            row.name = MadJson::getString(rowData, "label");
            row.code = MadJson::getInt(rowData, "code");
            row.offCode = MadJson::getInt(rowData, "off_code");
            row.mod = MadJson::getInt(rowData, "mod");

            // The ● dot + the row label share one text line above the button(s).
            auto dot = std::make_shared<TextComponent>(
                "○  " + MadJson::getString(rowData, "label"), Font::get(FONT_SIZE_SMALL),
                MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_CENTER, glm::ivec2 {0, 0});
            dot->setPosition(0.0f, mY);
            dot->setSize(mViewportSize.x, smallHeight);
            mScroll->addChild(dot.get());
            mWidgets.emplace_back(dot);
            row.dot = dot;
            mY += smallHeight;

            const std::string key {MadJson::getString(rowData, "key")};
            const std::string codeLabel {MadJson::getString(rowData, "code_label")};
            std::weak_ptr<int> alive {pageAlive()};
            // One picker per scope, flowing side by side on a single focus row.
            auto pickKey = [this, alive](const std::string& target,
                                         const std::string& title,
                                         const std::string& current,
                                         const bool modifier) {
                mPanel->pushPage(new GuiMadPageBackendChoice(
                    mPanel, title, "current: " + current,
                    modifier ? mModOptions : mActionOptions, "",
                    [this, alive, target](const std::string& value) {
                        if (alive.expired())
                            return;
                        pageRequest(
                            "sinden.set_keys",
                            [target, value](MadJson::Writer& writer) {
                                writer.Key("pairs");
                                writer.StartObject();
                                writer.Key(target.c_str(),
                                           static_cast<rapidjson::SizeType>(target.length()));
                                writer.String(value.c_str(),
                                              static_cast<rapidjson::SizeType>(value.length()));
                                writer.EndObject();
                            },
                            nullptr);
                    }));
            };
            std::vector<std::pair<std::string, std::function<void()>>> rowItems;
            rowItems.emplace_back(
                MadJson::getString(rowData, "label") + ":  " + codeLabel,
                [pickKey, key, codeLabel] {
                    pickKey(key, "Pick an action", codeLabel, false);
                });
            if (mShowOff) {
                const std::string offKey {MadJson::getString(rowData, "off_key")};
                const std::string offLabel {MadJson::getString(rowData, "off_label")};
                rowItems.emplace_back("off:  " + offLabel, [pickKey, offKey, offLabel] {
                    pickKey(offKey, "Offscreen action", offLabel, false);
                });
            }
            if (mShowMods) {
                const std::string modKey {MadJson::getString(rowData, "mod_key")};
                const std::string modLabel {MadJson::getString(rowData, "mod_label")};
                rowItems.emplace_back("mod:  " + modLabel, [pickKey, modKey, modLabel] {
                    pickKey(modKey, "Modifier", modLabel, true);
                });
            }
            addButtonRow(rowItems, false);
            mRows.emplace_back(row);
            mY += smallHeight * 0.2f;
        }
    }

    addButton("SAVE (RESTART DRIVER)", [this] {
        pageRequest("sinden.apply", nullptr,
                    [this](bool ok, const rapidjson::Value& payload) {
                        footer()->setStatus("");
                        footer()->flash(
                            MadJson::getString(payload, "message", "unknown error"), 5000,
                            !ok);
                    },
                    10000);
    });
    endColumn();
}

void GuiMadPageLightgunButtons::feedCode(const int code, const bool pressed)
{
    if (code == 0)
        return;
    for (Row& row : mRows) {
        bool hit {row.code == code};
        if (!hit && mShowOff && row.offCode == code)
            hit = true;
        if (hit && row.dot != nullptr) {
            row.dot->setText((pressed ? "●  " : "○  ") + row.name);
            row.dot->setColor(pressed ? MadTheme::color(MadColor::Green) : MadTheme::color(MadColor::Secondary));
        }
    }
}

bool GuiMadPageLightgunButtons::onKeyboardInput(InputConfig* config, Input input)
{
    // The driver synthesizes keystrokes from gun presses: feed the ● dots and
    // SWALLOW the whole keyboard (a gun mapped to Esc/Enter/arrows must never
    // navigate the panel — Tk parity). Both the plain and the swapped-case
    // code light (Shift/CapsLock can flip what the driver sends).
    const bool shift {(SDL_GetModState() & KMOD_SHIFT) != 0};
    const bool pressed {input.value != 0};
    feedCode(sindenCodeForKey(input.id, shift), pressed);
    feedCode(sindenCodeForKey(input.id, !shift), pressed);
    return true;
}

//  ── GuiMadPageLightgunBehavior ──

GuiMadPageLightgunBehavior::GuiMadPageLightgunBehavior(GuiMadPanel* panel, const int player)
    : MadLightgunPageBase {panel,
                           "P" + std::to_string(player) + " RECOIL & BEHAVIOR"}
    , mPlayer {player}
{
}

void GuiMadPageLightgunBehavior::build()
{
    setLoadingText("Loading…");
    const int player {mPlayer};
    pageRequest(
        "sinden.behavior",
        [player](MadJson::Writer& writer) {
            writer.Key("player");
            writer.Int(player);
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't read the gun config: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        });
}

void GuiMadPageLightgunBehavior::setKey(const std::string& base, const std::string& value)
{
    const std::string key {base + mSuffix};
    pageRequest(
        "sinden.set_keys",
        [key, value](MadJson::Writer& writer) {
            writer.Key("pairs");
            writer.StartObject();
            writer.Key(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
            writer.EndObject();
        },
        [this, key](bool ok, const rapidjson::Value& payload) {
            if (!ok)
                footer()->flash("Couldn't save " + key + ": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
        });
}

void GuiMadPageLightgunBehavior::rebuild(const rapidjson::Value& result)
{
    mSuffix = MadJson::getString(result, "suffix");

    beginColumn();
    auto recoil = addChips({{"r", "Enable recoil", MadJson::getBool(result, "recoil")}}, false);
    recoil->setOnToggle(
        [this](const std::string&, const bool on) { setKey("EnableRecoil", on ? "1" : "0"); });
    addStepper(
        "Recoil strength", 0.0f, 100.0f, 1.0f,
        [](const float value) { return std::to_string(static_cast<int>(std::lround(value))); },
        [this](const float value) {
            setKey("RecoilStrength", std::to_string(static_cast<int>(std::lround(value))));
        },
        static_cast<float>(MadJson::getInt(result, "strength", 100)));
    auto autoChip = addChips(
        {{"a", "Auto-fire recoil (machine-gun)", MadJson::getBool(result, "auto_recoil")}},
        false);
    autoChip->setOnToggle([this](const std::string&, const bool on) {
        setKey("TriggerRecoilNormalOrRepeat", on ? "1" : "0");
    });
    addStepper(
        "Auto recoil strength", 0.0f, 100.0f, 1.0f,
        [](const float value) { return std::to_string(static_cast<int>(std::lround(value))); },
        [this](const float value) {
            setKey("AutoRecoilStrength", std::to_string(static_cast<int>(std::lround(value))));
        },
        static_cast<float>(MadJson::getInt(result, "auto_strength", 40)));
    addStepper(
        "Auto recoil speed", 1.0f, 60.0f, 1.0f,
        [](const float value) { return std::to_string(static_cast<int>(std::lround(value))); },
        [this](const float value) {
            setKey("AutoRecoilDelayBetweenPulses",
                   std::to_string(static_cast<int>(std::lround(value))));
        },
        static_cast<float>(MadJson::getInt(result, "auto_speed", 13)));

    header("Other");
    const std::string handedLabel {MadJson::getString(result, "handedness_label", "?")};
    const std::string handed {MadJson::getString(result, "handedness", "2")};
    std::weak_ptr<int> alive {pageAlive()};
    addButton("HANDEDNESS:  " + handedLabel, [this, alive, handed] {
        mPanel->pushPage(new GuiMadPageBackendChoice(
            mPanel, "Handedness", "",
            {{"0", "Off"}, {"1", "Left-handed"}, {"2", "Right-handed"}}, handed,
            [this, alive](const std::string& value) {
                if (alive.expired())
                    return;
                setKey("GangstaSetting", value);
            }));
    });
    auto offReload = addChips(
        {{"o", "Offscreen reload", MadJson::getBool(result, "offscreen_reload")}}, false);
    offReload->setOnToggle([this](const std::string&, const bool on) {
        setKey("OffscreenReload", on ? "1" : "0");
    });

    addButton("SAVE & APPLY (RESTART DRIVER)", [this] {
        pageRequest("sinden.apply", nullptr,
                    [this](bool ok, const rapidjson::Value& payload) {
                        footer()->setStatus("");
                        footer()->flash(
                            MadJson::getString(payload, "message", "unknown error"), 5000,
                            !ok);
                    },
                    10000);
    });
    endColumn();
}

void GuiMadPageLightgunBehavior::onChildPopped()
{
    build(); // The handedness pick wrote the config; re-read for fresh labels.
}

//  ── GuiMadPageLightgunCamera ──

GuiMadPageLightgunCamera::GuiMadPageLightgunCamera(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "CAMERA TUNING"}
    , mPreviewLive {false}
    , mPollAccum {0}
    , mLastFrameMtimeNs {0}
{
}

GuiMadPageLightgunCamera::~GuiMadPageLightgunCamera()
{
    // The page can die any way (B, section switch, panel close): make sure the
    // daemon stops ffmpeg and restores the driver. Daemon teardown covers the
    // panel-close race; this covers in-panel navigation.
    if (!mStreamToken.empty())
        backend()->clearStreamCallback(mStreamToken);
    backend()->request("camera.preview_stop", nullptr, nullptr);
}

void GuiMadPageLightgunCamera::build()
{
    setLoadingText("Loading camera state…");
    pageRequest("camera.get", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        setLoadingText("");
        if (!ok) {
            footer()->setStatus("Couldn't read the camera config: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                true);
            return;
        }
        rebuild(payload);
    });
}

void GuiMadPageLightgunCamera::setCam(const int player, const std::string& ctrl,
                                      const int value, const bool isAuto,
                                      const bool autoValue)
{
    pageRequest(
        "camera.set",
        [player, ctrl, value, isAuto, autoValue](MadJson::Writer& writer) {
            writer.Key("player");
            writer.Int(player);
            writer.Key("ctrl");
            writer.String(ctrl.c_str(), static_cast<rapidjson::SizeType>(ctrl.length()));
            writer.Key("value");
            if (isAuto)
                writer.Bool(autoValue);
            else
                writer.Int(value);
        },
        nullptr);
    if (!mDirty) { // first adjustment: surface x=save
        mDirty = true;
        mPanel->refreshHelpPrompts();
    }
}

void GuiMadPageLightgunCamera::saveCamera()
{
    pageRequest(
        "camera.save", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            footer()->setStatus("");
            if (ok)
                mDirty = false;
            mPanel->refreshHelpPrompts();
            footer()->flash(MadJson::getString(payload, "message", "unknown error"), 5000, !ok);
        },
        10000);
}

bool GuiMadPageLightgunCamera::madSave()
{
    if (!mDirty)
        return false;
    saveCamera();
    return true;
}

bool GuiMadPageLightgunCamera::madCancel()
{
    if (!mDirty)
        return false;
    // Revert: camera.cancel re-seeds the buffer from the saved config and, while
    // previewing, re-applies those values to the live v4l2 controls. It returns
    // the reverted vals in camera.get's shape; rebuild() refreshes the steppers.
    pageRequest("camera.cancel", nullptr, [this](bool ok, const rapidjson::Value& payload) {
        if (!ok) {
            footer()->flash("Couldn't cancel: " +
                                MadJson::getString(payload, "message", "error"),
                            4000, true);
            return;
        }
        mDirty = false;
        rebuild(payload); // reseed steppers from the reverted values
        if (mPreviewLive) {
            // camera.cancel leaves the stream running, but rebuild() re-created the
            // hint with its default "press a Preview button" text; clear it and
            // force the next poll to redraw so the stale hint doesn't flash.
            if (mPreviewHint != nullptr)
                mPreviewHint->setText("");
            mLastFrameMtimeNs = 0;
        }
        mPanel->refreshHelpPrompts();
        footer()->flash("Reverted to saved.", 2500, false);
    });
    return true;
}

std::vector<HelpPrompt> GuiMadPageLightgunCamera::getHelpPrompts()
{
    // Override (not the shared base) so x=save / y=cancel appear on the camera
    // page ONLY — the sibling lightgun pages are not buffered.
    std::vector<HelpPrompt> prompts {MadLightgunPageBase::getHelpPrompts()};
    if (mDirty) {
        prompts.push_back(HelpPrompt("x", "save"));
        prompts.push_back(HelpPrompt("y", "cancel"));
    }
    return prompts;
}

void GuiMadPageLightgunCamera::togglePreview(const int player)
{
    const int target {player};
    pageRequest(
        "camera.preview",
        [target](MadJson::Writer& writer) {
            writer.Key("player");
            writer.Int(target);
        },
        [this, target](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Preview failed: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            if (MadJson::getBool(payload, "stopped"))
                return; // Second press: the stream's close event clears the image.
            mFramePath = MadJson::getString(payload, "path");
            const std::string token {MadJson::getString(payload, "stream")};
            if (!mStreamToken.empty())
                backend()->clearStreamCallback(mStreamToken);
            mStreamToken = token;
            std::weak_ptr<int> alive {pageAlive()};
            backend()->setStreamCallback(token, [this, alive,
                                                 target](const rapidjson::Value& data) {
                if (alive.expired())
                    return;
                if (MadJson::getBool(data, "closed")) {
                    mPreviewLive = false;
                    mLastFrameMtimeNs = 0;
                    if (mPreview != nullptr)
                        mPreview->setImage(""); // Drop the frozen last frame.
                    if (mPreviewHint != nullptr)
                        mPreviewHint->setText("( press a Preview button )");
                    // Drop the "Preview … live" sticky so the prompts return.
                    footer()->setStatus("");
                    return;
                }
                if (MadJson::getBool(data, "ready")) {
                    mPreviewLive = true;
                    footer()->setStatus(
                        "Preview P" + std::to_string(target) +
                        " live — adjust the sliders, then Save. (Press the button "
                        "again to stop. Aiming is OFF while tuning.)");
                    if (mPreviewHint != nullptr)
                        mPreviewHint->setText("");
                    return;
                }
                const std::string error {MadJson::getString(data, "error")};
                if (!error.empty()) {
                    footer()->flash("⚠ " + error, 5000, true);
                    return;
                }
                const std::string status {MadJson::getString(data, "status")};
                if (!status.empty())
                    footer()->setStatus(status);
            });
        },
        10000);
}

void GuiMadPageLightgunCamera::rebuild(const rapidjson::Value& result)
{
    const rapidjson::Value& vals {MadJson::getMember(result, "vals")};
    const rapidjson::Value& cams {MadJson::getMember(result, "cams")};

    beginColumn();
    // Controls take the LEFT half; the live image sits on the right (page
    // child, not scrolled).
    const float columnWidth {mViewportSize.x * 0.46f};
    mScroll->setSize(columnWidth, mViewportSize.y);

    addBlock("Aiming is OFF while tuning (the driver is paused so the camera is free). "
             "Press a Preview button, adjust while watching the feed, then Save. Goal: the "
             "white screen-border bright, the rest dark.",
             FONT_SIZE_MINI, MadTheme::color(MadColor::Primary),
             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);

    for (int player {1}; player <= 2; ++player) {
        const std::string playerKey {std::to_string(player)};
        const rapidjson::Value& v {MadJson::getMember(vals, playerKey.c_str())};
        header("Player " + playerKey + " gun  (" +
               MadJson::getString(cams, playerKey.c_str()) + ")");
        addButton("PREVIEW P" + playerKey + " GUN",
                  [this, player] { togglePreview(player); });
        addStepper(
            "Brightness", 0.0f, 255.0f, 1.0f,
            [](const float value) {
                return std::to_string(static_cast<int>(std::lround(value)));
            },
            [this, player](const float value) {
                setCam(player, "Brightness", static_cast<int>(std::lround(value)));
            },
            static_cast<float>(MadJson::getInt(v, "Brightness", 100)), 0.95f);
        addStepper(
            "Contrast", 0.0f, 255.0f, 1.0f,
            [](const float value) {
                return std::to_string(static_cast<int>(std::lround(value)));
            },
            [this, player](const float value) {
                setCam(player, "Contrast", static_cast<int>(std::lround(value)));
            },
            static_cast<float>(MadJson::getInt(v, "Contrast", 50)), 0.95f);
        auto autoChip =
            addChips({{"auto", "Auto exposure", MadJson::getBool(v, "auto")}}, false);
        autoChip->setOnToggle([this, player](const std::string&, const bool on) {
            setCam(player, "auto", 0, true, on);
        });
        addStepper(
            "Exposure (manual)", 10.0f, 2500.0f, 20.0f,
            [](const float value) {
                return std::to_string(static_cast<int>(std::lround(value)));
            },
            [this, player](const float value) {
                setCam(player, "Exposure", static_cast<int>(std::lround(value)));
            },
            static_cast<float>(MadJson::getInt(v, "Exposure", 80)), 0.95f);
    }

    addButton("SAVE", [this] { saveCamera(); });
    endColumn();

    // The preview area (right half): a placeholder hint + the image on top.
    mPreviewHint = std::make_shared<TextComponent>(
        "( press a Preview button )", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Secondary),
        ALIGN_CENTER, ALIGN_CENTER, glm::ivec2 {0, 0});
    mPreviewHint->setPosition(mViewportPos.x + columnWidth, mViewportPos.y);
    mPreviewHint->setSize(mViewportSize.x - columnWidth, mViewportSize.y * 0.5f);
    addChild(mPreviewHint.get());

    mPreview = std::make_shared<ImageComponent>();
    mPreview->setOrigin(0.5f, 0.0f);
    mPreview->setPosition(mViewportPos.x + columnWidth +
                              (mViewportSize.x - columnWidth) / 2.0f,
                          mViewportPos.y);
    mPreview->setMaxSize(mViewportSize.x - columnWidth - 8.0f, mViewportSize.y * 0.92f);
    addChild(mPreview.get());
}

void GuiMadPageLightgunCamera::pollFrame()
{
    struct stat st {};
    if (stat(mFramePath.c_str(), &st) != 0)
        return;
    const long long mtimeNs {static_cast<long long>(st.st_mtim.tv_sec) * 1000000000LL +
                             st.st_mtim.tv_nsec};
    if (mtimeNs == mLastFrameMtimeNs || st.st_size < 16)
        return;
    FILE* file {fopen(mFramePath.c_str(), "rb")};
    if (file == nullptr)
        return;
    int width {0}, height {0}, maxval {0};
    // ffmpeg writes "P6\n<w> <h>\n255\n" then raw RGB24.
    if (fscanf(file, "P6 %d %d %d", &width, &height, &maxval) != 3 || width <= 0 ||
        height <= 0 || width > 4096 || height > 4096 || maxval != 255) {
        fclose(file);
        return;
    }
    fgetc(file); // The single whitespace after the maxval.
    const size_t pixels {static_cast<size_t>(width) * static_cast<size_t>(height)};
    std::vector<unsigned char> rgb(pixels * 3);
    const size_t got {fread(rgb.data(), 1, rgb.size(), file)};
    fclose(file);
    if (got != rgb.size())
        return; // Mid-write frame — skip this tick (Tk parity).
    mFrameRgba.resize(pixels * 4);
    for (size_t i {0}; i < pixels; ++i) {
        mFrameRgba[i * 4 + 0] = rgb[i * 3 + 0];
        mFrameRgba[i * 4 + 1] = rgb[i * 3 + 1];
        mFrameRgba[i * 4 + 2] = rgb[i * 3 + 2];
        mFrameRgba[i * 4 + 3] = 0xFF;
    }
    mLastFrameMtimeNs = mtimeNs;
    mPreview->setRawImage(mFrameRgba.data(), static_cast<size_t>(width),
                          static_cast<size_t>(height));
}

void GuiMadPageLightgunCamera::update(int deltaTime)
{
    MadPage::update(deltaTime);
    if (!mPreviewLive || mFramePath.empty() || mPreview == nullptr)
        return;
    mPollAccum += deltaTime;
    if (mPollAccum < 66) // ~15 Hz, the Tk tick rate.
        return;
    mPollAccum = 0;
    pollFrame();
}
