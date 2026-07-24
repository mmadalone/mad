//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPagePostUpdate.cpp
//
//  MAD control panel: reapply system setup after a SteamOS update (deck-patches).
//

#include "guis/mad/pages/GuiMadPagePostUpdate.h"

#include "Sound.h"
#include "Window.h"
#include "components/ButtonComponent.h"
#include "guis/GuiTextEditKeyboardPopup.h"
#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "utils/PlatformUtil.h"

#include <algorithm>

namespace
{
    const char* kDesktopHint {"If this keeps failing, open Desktop Mode and run:  "
                              "~/Emulation/tools/launchers/deck-post-update.sh"};
}

GuiMadPagePostUpdate::GuiMadPagePostUpdate(GuiMadPanel* panel)
    : MadPage {panel, "REAPPLY SYSTEM SETUP"}
{
}

GuiMadPagePostUpdate::~GuiMadPagePostUpdate()
{
    if (!mRunToken.empty())
        backend()->clearStreamCallback(mRunToken);
}

bool GuiMadPagePostUpdate::onBackPressed()
{
    // The panel asks this BEFORE it pops/closes on Back. Block leaving while the sudo reapply runs.
    return mState == State::Running;
}

bool GuiMadPagePostUpdate::consumesSectionNav()
{
    // While running, take the shoulder/trigger buttons so the panel can't switch section away from
    // the in-progress reapply (input() swallows them).
    return mState == State::Running;
}

void GuiMadPagePostUpdate::build()
{
    const float fontH {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float gap {fontH * 0.5f};

    mIntro = std::make_shared<TextComponent>(
        "Checking what a SteamOS update reset…", Font::get(FONT_SIZE_SMALL),
        MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_TOP, glm::ivec2 {0, 1});
    addChild(mIntro.get());

    mStatus = std::make_shared<TextComponent>(
        "", Font::get(FONT_SIZE_SMALL), MadTheme::color(MadColor::Title), ALIGN_LEFT, ALIGN_CENTER,
        glm::ivec2 {0, 1});
    addChild(mStatus.get());

    mLog = std::make_shared<TextComponent>(
        "", Font::get(FONT_SIZE_MINI), MadTheme::color(MadColor::Secondary), ALIGN_LEFT, ALIGN_TOP,
        glm::ivec2 {0, 1});
    addChild(mLog.get());

    (void)gap;
    rebuildButtons();
    layout();
    fetchStatus();
}

void GuiMadPagePostUpdate::layout()
{
    const float fontH {Font::get(FONT_SIZE_SMALL)->getHeight()};
    const float gap {fontH * 0.5f};

    // Intro + status pinned to the top.
    float y {mViewportPos.y};
    const float introH {fontH * 4.0f};
    mIntro->setPosition(mViewportPos.x, y);
    mIntro->setSize(mViewportSize.x, introH);
    y += introH + gap * 0.4f;

    const float statusH {fontH * 1.4f};
    mStatus->setPosition(mViewportPos.x, y);
    mStatus->setSize(mViewportSize.x, statusH);
    y += statusH + gap * 0.4f;

    // Action row height.
    float rowH {0.0f};
    for (const auto& b : mButtons)
        rowH = std::max(rowH, b->getSize().y);

    const float bottom {mViewportPos.y + mViewportSize.y};
    // Before any run (Idle) there is no log, so don't strand the action at the very bottom with a
    // big empty gap - center it in the space below the intro/status. Once a run has produced log
    // output (Running/Done/DoneFailed) the row sits at the bottom so the log has the room above it.
    float buttonTop {bottom - rowH};
    if (mState == State::Idle)
        buttonTop = y + std::max(0.0f, (bottom - y - rowH) * 0.5f);

    if (!mButtons.empty()) {
        float x {mViewportPos.x};
        for (const auto& b : mButtons) {
            b->setPosition(x, buttonTop);
            x += b->getSize().x + gap;
        }
    }

    // Log fills between the status and the action row (or the bottom while idle/empty).
    const float logBottom {mState == State::Idle ? bottom : buttonTop - gap};
    mLog->setPosition(mViewportPos.x, y);
    mLog->setSize(mViewportSize.x, std::max(0.0f, logBottom - y));
}

void GuiMadPagePostUpdate::rebuildButtons()
{
    for (const auto& b : mButtons)
        removeChild(b.get());
    mButtons.clear();

    auto add = [this](const std::string& label, const std::function<void()>& cb) {
        auto b {std::make_shared<ButtonComponent>(label, label, cb)};
        addChild(b.get());
        mButtons.push_back(b);
    };

    // (No "LATER" button: Back (B) leaves the page when it isn't running; the pending flag persists
    // so the auto-offer keeps prompting until a reapply actually makes everything present again.)
    auto reapply = [this] {
        if (mPasswordless)
            startRun("");
        else
            promptPasswordThenRun();
    };
    switch (mState) {
        case State::Idle:
            add("REAPPLY NOW", reapply);
            break;
        case State::Running:
            break; // no actions while it runs (every exit route is blocked)
        case State::Done:
            add("REBOOT NOW", [this] { rebootNow(); });
            break;
        case State::DoneFailed:
            add("RE-RUN", reapply);
            add("REBOOT ANYWAY", [this] { rebootNow(); });
            break;
    }
    mFocus = 0;
    focusButton(0);
}

void GuiMadPagePostUpdate::focusButton(int index)
{
    if (mButtons.empty())
        return;
    mFocus = glm::clamp(index, 0, static_cast<int>(mButtons.size()) - 1);
    for (size_t i {0}; i < mButtons.size(); ++i)
        (static_cast<int>(i) == mFocus) ? mButtons[i]->onFocusGained() : mButtons[i]->onFocusLost();
    mPanel->refreshHelpPrompts();
}

void GuiMadPagePostUpdate::fetchStatus()
{
    std::weak_ptr<int> alive {pageAlive()};
    pageRequest("postupdate.status", nullptr,
                [this, alive](bool ok, const rapidjson::Value& payload) {
                    if (alive.expired() || !ok)
                        return;
                    mMissing.clear();
                    if (payload.HasMember("missing") && payload["missing"].IsArray())
                        for (const auto& v : payload["missing"].GetArray())
                            if (v.IsString())
                                mMissing.push_back(v.GetString());
                    mPasswordless = MadJson::getBool(payload, "sudo_passwordless");
                    // "needed" = an update actually wiped something. When the page is opened
                    // manually with nothing pending, do NOT claim an update reset the system.
                    const bool pending {MadJson::getBool(payload, "pending")};
                    const bool needed {pending || !mMissing.empty()};
                    mStatusLoaded = true;

                    std::string intro;
                    if (needed) {
                        intro = "A SteamOS update reset system files. This re-applies them "
                                "(Samba, lightgun, controllers, suspend mode).";
                        if (!mMissing.empty()) {
                            intro += "\nMissing now: ";
                            for (size_t i {0}; i < mMissing.size(); ++i)
                                intro += (i ? ", " : "") + mMissing[i];
                        }
                    }
                    else {
                        intro = "Your system setup looks complete - nothing needs reapplying "
                                "right now.\nRun this after a SteamOS update if Samba shares, the "
                                "lightgun, controllers, or suspend mode stop working.";
                    }
                    mIntro->setText(intro);
                    if (needed)
                        setStatus(mPasswordless
                                      ? "Ready (passwordless sudo). Press REAPPLY NOW."
                                      : "Press REAPPLY NOW - you'll enter your Steam Deck password.");
                    else
                        setStatus(mPasswordless
                                      ? "Nothing to do - you can still reapply (safe to re-run)."
                                      : "Nothing to do - you can still reapply (you'll enter your "
                                        "Steam Deck password).");
                });
}

void GuiMadPagePostUpdate::promptPasswordThenRun()
{
    std::weak_ptr<int> alive {pageAlive()};
    mWindow->pushGui(new GuiTextEditKeyboardPopup(
        0.0f, "SUDO PASSWORD", "",
        [this, alive](const std::string& text) {
            if (alive.expired())
                return;
            if (text.empty()) {
                setStatus("No password entered.");
                return;
            }
            startRun(text);
        },
        false, "OK", "APPLY?", "Enter your Steam Deck (sudo) password", "", "LOAD DEFAULT", "CLEAR",
        "CANCEL", /*maskDisplay=*/true));
}

void GuiMadPagePostUpdate::startRun(const std::string& password)
{
    if (mState == State::Running)
        return;
    mState = State::Running;
    mLogLines.clear();
    mLog->setText("");
    setStatus("Reapplying… do not close ES-DE.");
    rebuildButtons();
    layout();

    std::weak_ptr<int> alive {pageAlive()};
    pageRequest(
        "postupdate.run",
        [password](MadJson::Writer& w) {
            w.Key("password");
            w.String(password.c_str(), static_cast<rapidjson::SizeType>(password.length()));
        },
        [this, alive](bool ok, const rapidjson::Value& payload) {
            if (alive.expired())
                return;
            if (!ok) {
                mState = State::Idle;
                setStatus("Couldn't start: " + MadJson::getString(payload, "message", "error"));
                rebuildButtons();
                layout();
                return;
            }
            installStream(MadJson::getString(payload, "stream"));
        },
        30000);
}

void GuiMadPagePostUpdate::installStream(const std::string& token)
{
    mRunToken = token;
    if (token.empty())
        return;
    std::weak_ptr<int> alive {pageAlive()};
    backend()->setStreamCallback(token, [this, alive](const rapidjson::Value& data) {
        if (alive.expired())
            return;
        if (MadJson::getBool(data, "closed")) {
            if (mState == State::Running) {
                mState = State::DoneFailed;
                setStatus("The reapply ended unexpectedly.");
                rebuildButtons();
                layout();
            }
            return;
        }
        if (MadJson::getBool(data, "auth_failed")) {
            --mTriesLeft;
            mState = State::Idle;
            if (mTriesLeft > 0) {
                setStatus("Wrong password - try again (" + std::to_string(mTriesLeft) + " left).");
                rebuildButtons();
                layout();
                promptPasswordThenRun();
            }
            else {
                setStatus(std::string("Wrong password too many times. ") + kDesktopHint);
                rebuildButtons();
                layout();
            }
            return;
        }
        if (MadJson::getBool(data, "done")) {
            mFailed.clear();
            if (data.HasMember("failed") && data["failed"].IsArray())
                for (const auto& v : data["failed"].GetArray())
                    if (v.IsString())
                        mFailed.push_back(v.GetString());
            if (mFailed.empty()) {
                mState = State::Done;
                setStatus("Done. Reboot to finish applying everything.");
            }
            else {
                mState = State::DoneFailed;
                std::string f;
                for (size_t i {0}; i < mFailed.size(); ++i)
                    f += (i ? ", " : "") + mFailed[i];
                setStatus("Some steps failed: " + f + ". You can re-run. " + kDesktopHint);
            }
            rebuildButtons();
            layout();
            return;
        }
        const std::string line {MadJson::getString(data, "line")};
        if (!line.empty())
            appendLog(line);
    });
}

void GuiMadPagePostUpdate::appendLog(const std::string& line)
{
    mLogLines.push_back(line);
    while (static_cast<int>(mLogLines.size()) > kMaxLogLines)
        mLogLines.pop_front();
    std::string joined;
    for (const auto& l : mLogLines)
        joined += (joined.empty() ? "" : "\n") + l;
    mLog->setText(joined);
}

void GuiMadPagePostUpdate::setStatus(const std::string& text)
{
    if (mStatus != nullptr)
        mStatus->setText(text);
    footer()->setStatus(text);
}

void GuiMadPagePostUpdate::rebootNow()
{
    Utils::Platform::quitES(Utils::Platform::QuitMode::REBOOT);
}

bool GuiMadPagePostUpdate::input(InputConfig* config, Input input)
{
    // Never leave / act mid-run: swallow every button press (Back is blocked by onBackPressed(),
    // section nav by consumesSectionNav(); this stops button activation too).
    if (mState == State::Running)
        return input.value != 0;
    if (mButtons.empty())
        return false;
    if (mButtons[mFocus]->input(config, input))
        return true;
    if (input.value == 0)
        return false;
    if (config->isMappedLike("left", input) && mFocus > 0) {
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        focusButton(mFocus - 1);
        return true;
    }
    if (config->isMappedLike("right", input) && mFocus < static_cast<int>(mButtons.size()) - 1) {
        NavigationSounds::getInstance().playThemeNavigationSound(SCROLLSOUND);
        focusButton(mFocus + 1);
        return true;
    }
    return false;
}

std::vector<HelpPrompt> GuiMadPagePostUpdate::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;
    if (mState == State::Running)
        return prompts; // no navigation while it runs
    if (mButtons.size() > 1)
        prompts.push_back(HelpPrompt("left/right", "choose"));
    prompts.push_back(HelpPrompt("a", "select"));
    prompts.push_back(HelpPrompt("b", "back"));
    return prompts;
}
