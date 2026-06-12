//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPanel.cpp
//
//  MAD control panel (deck-patches): fullscreen, ES-DE-native shell around the
//  mad-backend.py daemon.
//

#include "guis/mad/GuiMadPanel.h"

#include "Sound.h"
#include "guis/mad/pages/GuiMadPageBackends.h"
#include "guis/mad/pages/GuiMadPageDaphne.h"
#include "guis/mad/pages/GuiMadPageLightgun.h"
#include "guis/mad/pages/GuiMadPagePlayers.h"
#include "guis/mad/pages/GuiMadPagePreview.h"
#include "guis/mad/pages/GuiMadPagePriority.h"
#include "guis/mad/pages/GuiMadPageQuitCombo.h"
#include "guis/mad/pages/GuiMadPageSplash.h"
#include "guis/mad/pages/GuiMadPageSystems.h"
#include "utils/FileSystemUtil.h"
#include "utils/PlatformUtil.h"
#include "utils/StringUtil.h"

namespace
{
    // Placeholder page for sections not yet ported natively; offers the classic
    // Tk control panel as a fallback (Tk is NOT auto-launched on section switch).
    class MadLegacyPage : public MadPage
    {
    public:
        MadLegacyPage(GuiMadPanel* panel, const std::string& sectionLabel)
            : MadPage {panel, Utils::String::toUpper(sectionLabel)}
        {
        }

        void build() override
        {
            mInfo = std::make_shared<TextComponent>(
                "This section isn't ported to the native panel yet.",
                Font::get(FONT_SIZE_MEDIUM), mMenuColorSecondary, ALIGN_CENTER, ALIGN_CENTER,
                glm::ivec2 {0, 0});
            mInfo->setPosition(mViewportPos.x, mViewportPos.y + mViewportSize.y * 0.25f);
            mInfo->setSize(mViewportSize.x, Font::get(FONT_SIZE_MEDIUM)->getHeight());
            addChild(mInfo.get());

            mButton = std::make_shared<ButtonComponent>("OPEN CLASSIC MAD (Tk)",
                                                        "open classic MAD",
                                                        [this] { mPanel->launchClassicMad(); });
            mButton->setPosition(
                mViewportPos.x + (mViewportSize.x - mButton->getSize().x) / 2.0f,
                mInfo->getPosition().y + mInfo->getSize().y + mViewportSize.y * 0.08f);
            mButton->onFocusGained();
            addChild(mButton.get());
        }

        bool input(InputConfig* config, Input input) override
        {
            if (mButton != nullptr)
                return mButton->input(config, input);
            return false;
        }

        std::vector<HelpPrompt> getHelpPrompts() override
        {
            std::vector<HelpPrompt> prompts;
            prompts.push_back(HelpPrompt("a", "open classic MAD"));
            return prompts;
        }

    private:
        std::shared_ptr<TextComponent> mInfo;
        std::shared_ptr<ButtonComponent> mButton;
    };
} // namespace

GuiMadPanel::GuiMadPanel()
    : mRenderer {Renderer::getInstance()}
    , mCurrentSection {0}
    , mPanelState {PanelState::Connecting}
    , mSidebarWidth {0.0f}
    , mHelpReserve {0.0f}
    , mClassicLaunchPending {false}
    , mInputLocked {false}
{
    setPosition(0.0f, 0.0f);
    setSize(Renderer::getScreenWidth(), Renderer::getScreenHeight());

    // Section registry. Phase 3 adds Lightgun + Daphne natively; the testers
    // (X-Arcade/Gamepads) and Backup fall back to the classic Tk app via
    // MadLegacyPage until phases 4/5 land.
    mSections = {{"Preview", "preview", true},   {"Systems", "systems", true},
                 {"Priority", "priority", true}, {"Players", "players", true},
                 {"Quit combo", "quit-combo", true}, {"Backends", "backends", true},
                 {"Lightgun", "lightgun", true}, {"Daphne", "daphne", true},
                 {"X-Arcade", "x-arcade", false}, {"Gamepads", "gamepads", false},
                 {"Splash", "splash", true},      {"Backup", "backup", false}};

    mSidebarWidth = mSize.x * 0.14f;
    const float padding {mSize.y * 0.025f};
    // Keep clear of ES-DE's help row at the very bottom of the screen.
    mHelpReserve = mSize.y * 0.055f;
    const float footerHeight {Font::get(FONT_SIZE_SMALL)->getHeight() * 1.2f};

    std::vector<std::string> labels;
    for (const Section& section : mSections)
        labels.emplace_back(section.label);

    mSidebar = std::make_unique<MadSidebar>(labels);
    mSidebar->setPosition(0.0f, 0.0f);
    mSidebar->setSize(mSidebarWidth, mSize.y - mHelpReserve);
    addChild(mSidebar.get());

    mFooter = std::make_unique<MadFooter>();
    mFooter->setPosition(mSidebarWidth + padding, mSize.y - mHelpReserve - footerHeight);
    mFooter->setSize(mSize.x - mSidebarWidth - padding * 2.0f, footerHeight);
    addChild(mFooter.get());

    mContentPos = {mSidebarWidth + padding, padding};
    mContentSize = {mSize.x - mSidebarWidth - padding * 2.0f,
                    mFooter->getPosition().y - padding * 2.0f};

    // Connecting/error status; rendered manually per panel state (not a child).
    mStatusText = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_MEDIUM),
                                                  mMenuColorPrimary, ALIGN_CENTER, ALIGN_CENTER,
                                                  glm::ivec2 {0, 1});
    mStatusText->setPosition(mContentPos.x + mContentSize.x * 0.1f,
                             mContentPos.y + mContentSize.y * 0.25f);
    mStatusText->setSize(mContentSize.x * 0.8f, 0.0f);

    mRetryButton = std::make_shared<ButtonComponent>("RETRY", "retry", [this] {
        mBackend->restart();
        showConnecting();
    });
    mRetryButton->onFocusGained();

    mBusy.setPosition(mContentPos.x, mContentPos.y);
    mBusy.setSize(mContentSize.x, mContentSize.y);
    mBusy.setText("Starting MAD backend…");
    mBusy.onSizeChanged();

    // Preview is native as of phase 1 — restore the spec-order landing.
    mCurrentSection = 0;
    mSidebar->setActive(mCurrentSection);

    mBackend = std::make_unique<MadBackend>();
    mBackend->setOnReady([this] { onBackendReady(); });
    // Capture-stream input lock: the modal handles its own input (it's
    // window-topmost) but the panel must swallow anything else while locked.
    mBackend->setEventCallback("input.lock", [this](const rapidjson::Value& data) {
        mInputLocked = MadJson::getBool(data, "locked", false);
    });
    showConnecting();
    mBackend->spawn();
}

void GuiMadPanel::onBackendReady()
{
    mPanelState = PanelState::Ready;
    // A backend death mid-capture must not leave the panel locked forever.
    mInputLocked = false;
    // The fresh daemon's stream-token counter restarts at s1 and the old
    // subscribers were dropped in shutdownChild() — forget the old watch token
    // so ensureDeviceWatch() re-registers cleanly instead of early-returning
    // on a token match.
    mDeviceWatchToken.clear();
    // Re-request on every (re)connect: a backend death before the art.resolve
    // response must not leave the sidebar label-only for the whole session.
    // art.resolve is cheap and idempotent.
    requestSidebarIcons();
    // (Re)build the current section — this also runs after a backend restart
    // following a classic Tk session or a RETRY.
    switchSection(mCurrentSection);
}

void GuiMadPanel::showConnecting()
{
    mPanelState = PanelState::Connecting;
    mPageStack.clear();
    mStatusText->setText("Starting MAD backend…");
    mFooter->setStatus("");
    updateHelpPrompts();
}

void GuiMadPanel::showError(const std::string& message)
{
    mPanelState = PanelState::Errored;
    mInputLocked = false;
    mPageStack.clear();
    mStatusText->setText(message);
    // The button sits below the (wrapped) error text.
    mRetryButton->setPosition(
        mContentPos.x + (mContentSize.x - mRetryButton->getSize().x) / 2.0f,
        mStatusText->getPosition().y + mStatusText->getSize().y + mContentSize.y * 0.06f);
    mFooter->setStatus("MAD backend error", true);
    updateHelpPrompts();
}

void GuiMadPanel::requestSidebarIcons()
{
    // One art.resolve call resolves every sidebar icon through the backend's
    // theme → launchers-art → esde-build-art chain (mirrors the Tk sidebar).
    std::vector<Section> sections {mSections};
    mBackend->request(
        "art.resolve",
        [sections](MadJson::Writer& writer) {
            writer.Key("names");
            writer.StartObject();
            for (const Section& section : sections) {
                writer.Key(section.artKey.c_str(),
                           static_cast<rapidjson::SizeType>(section.artKey.length()));
                writer.StartArray();
                const std::string iconPath {"icons/" + section.artKey + ".png"};
                const std::string flatPath {section.artKey + ".png"};
                writer.String(iconPath.c_str(),
                              static_cast<rapidjson::SizeType>(iconPath.length()));
                writer.String(flatPath.c_str(),
                              static_cast<rapidjson::SizeType>(flatPath.length()));
                if (section.artKey == "x-arcade")
                    writer.String("icons/x-arcade-sidebar.png");
                if (section.artKey == "gamepads")
                    writer.String("icons/genericgamepad.png");
                // Last-resort: the active theme's console.png via the backend's
                // console: resolver (mirrors the Tk sidebar's daphne fallback).
                if (section.artKey == "daphne")
                    writer.String("console:daphne");
                writer.EndArray();
            }
            writer.EndObject();
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok)
                return; // Label-only sidebar.
            const rapidjson::Value& paths {MadJson::getMember(payload, "paths")};
            for (size_t i {0}; i < mSections.size(); ++i) {
                const std::string path {
                    MadJson::getString(paths, mSections[i].artKey.c_str())};
                if (!path.empty())
                    mSidebar->setIcon(static_cast<int>(i), path);
            }
        });
}

void GuiMadPanel::switchSection(const int index)
{
    mCurrentSection = index;
    mSidebar->setActive(index);
    // The sticky status belongs to the old section's pages — don't leak it.
    mFooter->setStatus("");
    mPageStack.clear();
    MadPage* root {makeRootPage(index)};
    preparePage(root);
    mPageStack.emplace_back(root);
    updateHelpPrompts();
}

MadPage* GuiMadPanel::makeRootPage(const int index)
{
    const Section& section {mSections[index]};
    if (section.label == "Preview")
        return new GuiMadPagePreview(this);
    if (section.label == "Systems")
        return new GuiMadPageSystems(this);
    if (section.label == "Priority")
        return new GuiMadPagePriority(this);
    if (section.label == "Players")
        return new GuiMadPagePlayers(this);
    if (section.label == "Quit combo")
        return new GuiMadPageQuitCombo(this);
    if (section.label == "Backends")
        return new GuiMadPageBackends(this);
    if (section.label == "Lightgun")
        return new GuiMadPageLightgun(this);
    if (section.label == "Daphne")
        return new GuiMadPageDaphne(this);
    if (section.label == "Splash")
        return new GuiMadPageSplash(this);
    return new MadLegacyPage(this, section.label);
}

void GuiMadPanel::ensureDeviceWatch()
{
    // Safe to call on every page build: the backend keeps one watch stream and
    // returns the same token with already:true. After a backend restart the
    // token may change, in which case the callback re-attaches.
    mBackend->request("devices.watch", nullptr,
                      [this](bool ok, const rapidjson::Value& payload) {
                          if (!ok)
                              return;
                          const std::string token {MadJson::getString(payload, "stream")};
                          if (token.empty() || token == mDeviceWatchToken)
                              return;
                          if (!mDeviceWatchToken.empty())
                              mBackend->clearStreamCallback(mDeviceWatchToken);
                          mDeviceWatchToken = token;
                          mBackend->setStreamCallback(
                              token, [this](const rapidjson::Value& data) {
                                  if (MadJson::getBool(data, "closed", false))
                                      return;
                                  if (mPanelState == PanelState::Ready &&
                                      currentPage() != nullptr)
                                      currentPage()->onDevicesChanged(data);
                              });
                      });
}

void GuiMadPanel::preparePage(MadPage* page)
{
    page->setPosition(mContentPos.x, mContentPos.y);
    page->setSize(mContentSize.x, mContentSize.y);
    page->build();
}

void GuiMadPanel::pushPage(MadPage* page)
{
    if (currentPage() != nullptr)
        currentPage()->onSaveFocus();
    preparePage(page);
    mPageStack.emplace_back(page);
    updateHelpPrompts();
}

void GuiMadPanel::popPage()
{
    if (mPageStack.size() <= 1)
        return;
    mPageStack.pop_back();
    currentPage()->onRestoreFocus();
    // The popped child may have changed what the revealed page displays (e.g.
    // detail-page toggles flip the Systems grid's ● badge truth).
    currentPage()->onChildPopped();
    updateHelpPrompts();
}

bool GuiMadPanel::input(InputConfig* config, Input input)
{
    // Tk parity, panel-GLOBAL: the keyboard NEVER navigates or activates MAD —
    // the Sinden driver synthesizes display-server keystrokes from gun presses
    // (arrows/Return/Escape would browse, pick, and close). The button-map
    // page still receives the events to light its ● dots.
    if (input.device == DEVICE_KEYBOARD) {
        if (mPanelState == PanelState::Ready && !mInputLocked && currentPage() != nullptr)
            currentPage()->onKeyboardInput(config, input);
        return true;
    }

    if (mPanelState == PanelState::Errored) {
        if (input.value != 0 && config->isMappedTo("b", input)) {
            NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
            delete this;
            return true;
        }
        mRetryButton->input(config, input);
        return true;
    }

    if (mPanelState == PanelState::Connecting) {
        if (input.value != 0 && config->isMappedTo("b", input)) {
            NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
            delete this;
        }
        return true;
    }

    // A capture stream is live: the press the daemon is reading also reaches
    // SDL. The capture modal (window-topmost) handles its own input; anything
    // that still gets here must not move the panel.
    if (mInputLocked)
        return true;

    if (input.value != 0) {
        if (config->isMappedTo("b", input)) {
            // The page may consume B itself (e.g. cancel a reorder carry).
            if (currentPage() != nullptr && currentPage()->onBackPressed())
                return true;
            NavigationSounds::getInstance().playThemeNavigationSound(BACKSOUND);
            if (mPageStack.size() > 1)
                popPage();
            else
                delete this; // Back to the Utilities menu.
            return true;
        }
        if (config->isMappedLike("leftshoulder", input)) {
            NavigationSounds::getInstance().playThemeNavigationSound(SYSTEMBROWSESOUND);
            switchSection((mCurrentSection + static_cast<int>(mSections.size()) - 1) %
                          static_cast<int>(mSections.size()));
            return true;
        }
        if (config->isMappedLike("rightshoulder", input)) {
            NavigationSounds::getInstance().playThemeNavigationSound(SYSTEMBROWSESOUND);
            switchSection((mCurrentSection + 1) % static_cast<int>(mSections.size()));
            return true;
        }
        if (config->isMappedLike("lefttrigger", input)) {
            if (currentPage() != nullptr)
                currentPage()->pageScroll(-1);
            return true;
        }
        if (config->isMappedLike("righttrigger", input)) {
            if (currentPage() != nullptr)
                currentPage()->pageScroll(1);
            return true;
        }
    }

    if (currentPage() != nullptr)
        currentPage()->input(config, input);

    // Swallow everything else — input must never leak to the ES-DE views below.
    return true;
}

void GuiMadPanel::update(int deltaTime)
{
    if (mClassicLaunchPending) {
        mClassicLaunchPending = false;
        // The Tk app reads evdev/SDL itself — stop the backend FIRST so there's
        // no device contention. The page stack is also cleared (showConnecting)
        // before the blocking launch, which is why this runs from update() and
        // not from the legacy page's own input frame.
        mBackend->terminate();
        showConnecting();
        // Blocking, exactly the call the Utilities row shipped with: ES-DE stays
        // alive in the background and keeps handling controller hotplug.
        Utils::Platform::launchGameUnix(
            Utils::FileSystem::getHomePath() + "/Emulation/tools/launchers/MAD.sh", "", false);
        // Back from Tk: reconnect; onBackendReady() rebuilds the current section.
        mBackend->restart();
    }

    // Response/event callbacks only fire from this poll(), i.e. while the panel
    // is topmost (Window only updates the top GUI). The backend's reader thread
    // keeps draining the pipe regardless, so the daemon never blocks on a full
    // pipe even when a GuiMsgBox or similar covers the panel.
    mBackend->poll();

    if (mBackend->state() == MadBackend::State::Errored && mPanelState != PanelState::Errored)
        showError(mBackend->errorMessage());

    if (mPanelState == PanelState::Connecting)
        mBusy.update(deltaTime);

    if (mPanelState == PanelState::Ready && currentPage() != nullptr)
        currentPage()->update(deltaTime);

    GuiComponent::update(deltaTime);
}

void GuiMadPanel::render(const glm::mat4& parentTrans)
{
    glm::mat4 trans {parentTrans * getTransform()};
    mRenderer->setMatrix(trans);

    // Fully opaque, flat menu-scheme background — the gamelist view behind the
    // panel must not show through. Stop above the reserved bottom strip so
    // ES-DE's standard underdraw + help-prompt row (drawn by Window::render()
    // BEFORE the top GUI) stays visible.
    mRenderer->drawRect(0.0f, 0.0f, mSize.x, mSize.y - mHelpReserve, mMenuColorFrame,
                        mMenuColorFrame);
    // Thin separator between the sidebar and the content area.
    mRenderer->drawRect(mSidebarWidth, 0.0f, std::max(1.0f, mSize.x * 0.0012f),
                        mSize.y - mHelpReserve, mMenuColorSeparators, mMenuColorSeparators);

    renderChildren(trans);

    if (mPanelState == PanelState::Ready && currentPage() != nullptr)
        currentPage()->render(trans);

    if (mPanelState == PanelState::Connecting) {
        mStatusText->render(trans);
        mBusy.render(trans);
    }
    else if (mPanelState == PanelState::Errored) {
        mStatusText->render(trans);
        mRetryButton->render(trans);
    }
}

std::vector<HelpPrompt> GuiMadPanel::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;

    if (mPanelState == PanelState::Errored) {
        prompts.push_back(HelpPrompt("a", "retry"));
        prompts.push_back(HelpPrompt("b", "back"));
        return prompts;
    }
    if (mPanelState == PanelState::Connecting) {
        prompts.push_back(HelpPrompt("b", "back"));
        return prompts;
    }

    if (currentPage() != nullptr)
        prompts = currentPage()->getHelpPrompts();
    prompts.push_back(HelpPrompt("lr", "section"));
    prompts.push_back(HelpPrompt("b", mPageStack.size() > 1 ? "back" : "close"));
    return prompts;
}
