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
#include "guis/mad/MadWiiBridge.h"
#include "guis/mad/pages/GuiMadPageBackup.h"
#include "guis/mad/pages/GuiMadPageGamepads.h"
#include "guis/mad/pages/GuiMadPageLightgun.h"
#include "guis/mad/pages/GuiMadPagePlayers.h"
#include "guis/mad/pages/GuiMadPagePreview.h"
#include "guis/mad/pages/GuiMadPagePriority.h"
#include "guis/mad/pages/GuiMadPageQuitCombo.h"
#include "guis/mad/pages/GuiMadPageSplash.h"
#include "guis/mad/pages/GuiMadPageSystems.h"
#include "guis/mad/pages/GuiMadPageXArcade.h"
#include "guis/mad/pages/GuiMadPageBezelProject.h"
#include "guis/mad/pages/GuiMadPageRetroArch.h"
#include "guis/mad/pages/GuiMadPageStandalones.h"
#include "guis/mad/MadTheme.h"

GuiMadPanel::GuiMadPanel()
    : mRenderer {Renderer::getInstance()}
    , mCurrentSection {0}
    , mPanelState {PanelState::Connecting}
    , mSidebarWidth {0.0f}
    , mHelpReserve {0.0f}
    , mInputLocked {false}
    , mInputLockAllowNav {false}
{
    setPosition(0.0f, 0.0f);
    setSize(Renderer::getScreenWidth(), Renderer::getScreenHeight());

    // MAD theming: (re)load the active theme's router-config/*-theme.xml
    // BEFORE any panel widget is created. The injected defaults are the
    // CURRENT menu-scheme values (these statics are protected, and reading
    // them here also tracks the dark/light scheme).
    MadTheme::getInstance().load({
        {MadColor::Frame, mMenuColorFrame},
        {MadColor::Primary, mMenuColorPrimary},
        {MadColor::Secondary, mMenuColorSecondary},
        {MadColor::Title, mMenuColorTitle},
        {MadColor::Selector, mMenuColorSelector},
        {MadColor::Red, mMenuColorRed},
        {MadColor::Green, mMenuColorGreen},
        {MadColor::Separators, mMenuColorSeparators},
        {MadColor::PanelDimmed, mMenuColorPanelDimmed},
        {MadColor::ButtonFlatUnfocused, mMenuColorButtonFlatUnfocused},
        {MadColor::HelpText, 0x777777FF},
    });

    // Section registry — every section is native (the classic Tk control
    // panel was retired in phase 5B; router-config-gui.py stays in the repo
    // as the behavioral reference, it just isn't launched anymore).
    // Backends / Daphne / Model 2 are now reached through the Standalones hub
    // (each emulator's tile), so they're no longer top-level sidebar sections.
    mSections = {{"Preview", "preview"},   {"Systems", "systems"},
                 {"Priority", "priority"}, {"Players", "players"},
                 {"Quit combo", "quit-combo"},
                 {"Lightgun", "lightgun"},
                 {"Standalones", "standalones"}, {"RetroArch", "retroarch"},
                 {"Bezel Project", "bezelproject"},
                 {"X-Arcade", "x-arcade"},
                 {"Gamepads", "gamepads"}, {"Splash", "splash"},
                 {"Backup", "backup"}};

    const float padding {mSize.y * 0.025f};
    // ES-DE's help row at the very bottom of the screen — shared with the
    // footer: while the footer has text the panel suppresses the prompts.
    mHelpReserve = mSize.y * 0.055f;
    refreshThemedBackground();

    std::vector<std::string> labels;
    for (const Section& section : mSections)
        labels.emplace_back(section.label);

    // Sidebar hugs its content (icon box or the widest label, plus margins)
    // instead of a fixed 14% column with dead space at the sides.
    float maxLabelWidth {0.0f};
    for (const std::string& label : labels)
        maxLabelWidth =
            std::max(maxLabelWidth, Font::get(FONT_SIZE_MINI)->sizeText(label).x);
    const float iconBox {Renderer::getScreenHeight() * 0.14f};
    const float sidebarMargin {Renderer::getScreenHeight() * 0.024f};
    mSidebarWidth =
        std::min(mSize.x * 0.14f, std::max(iconBox, maxLabelWidth) + sidebarMargin);

    mSidebar = std::make_unique<MadSidebar>(labels);
    mSidebar->setPosition(0.0f, 0.0f);
    mSidebar->setSize(mSidebarWidth, mSize.y - mHelpReserve);
    addChild(mSidebar.get());
    // Themed sidebar icons are local files — show them from the first frame
    // instead of waiting for the backend (requestSidebarIcons re-applies the
    // same precedence once the art chain answers).
    for (size_t i {0}; i < mSections.size(); ++i) {
        const std::string themed {MadTheme::pageIconPath(mSections[i].artKey, "sidebar")};
        if (!themed.empty())
            mSidebar->setIcon(static_cast<int>(i), themed);
    }

    mFooter = std::make_unique<MadFooter>();
    mFooter->setPosition(0.0f, mSize.y - mHelpReserve);
    mFooter->setSize(mSize.x, mHelpReserve);
    // While the footer has text the prompts yield the strip (and return the
    // moment it clears) — one fully dynamic help row.
    mFooter->setOnVisibilityChanged([this] { updateHelpPrompts(); });
    addChild(mFooter.get());

    // Our own help row sits on the themed strip; color it like the footer.
    mStripHelp.setHelpTextColor(MadTheme::color(MadColor::HelpText));
    mStripHelp.setHelpIconColor(MadTheme::color(MadColor::HelpText));

    mContentPos = {mSidebarWidth + padding, padding};
    mContentSize = {mSize.x - mSidebarWidth - padding * 2.0f,
                    mSize.y - mHelpReserve - padding * 2.0f};

    // Connecting/error status; rendered manually per panel state (not a child).
    mStatusText = std::make_shared<TextComponent>("", Font::get(FONT_SIZE_MEDIUM),
                                                  MadTheme::color(MadColor::Primary), ALIGN_CENTER, ALIGN_CENTER,
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
        // Testers set "nav":true — the tested pad is grabbed, so let other pads
        // still navigate (reach STOP). Captures omit it and stay swallowed.
        mInputLockAllowNav = mInputLocked && MadJson::getBool(data, "nav", false);
    });
    showConnecting();
    mBackend->spawn();
}

void GuiMadPanel::onBackendReady()
{
    LOG(LogInfo) << "GuiMadPanel: backend ready (backend stderr -> "
                    "~/Emulation/storage/controller-router/mad-backend.log)";
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
    // Hard clear (flash included): outside Ready the prompts aren't
    // suppressed, so leftover flash text would overlap them in the strip.
    mFooter->clear();
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
    // Hard clear (flash included): the centered text already explains the
    // error, and outside Ready leftover footer text would overlap the
    // "A retry / B back" prompts in the strip.
    mFooter->clear();
    updateHelpPrompts();
}

void GuiMadPanel::requestSidebarIcons()
{
    // MAD theme XMLs win first: <icon name="sidebar"> in a page's theme file
    // (or the global one) replaces that section's sidebar icon outright.
    for (size_t i {0}; i < mSections.size(); ++i) {
        const std::string themed {
            MadTheme::pageIconPath(mSections[i].artKey, "sidebar")};
        if (!themed.empty())
            mSidebar->setIcon(static_cast<int>(i), themed);
    }

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
                // A MAD-theme sidebar icon outranks the backend art chain.
                if (!MadTheme::pageIconPath(mSections[i].artKey, "sidebar").empty())
                    continue;
                const std::string path {
                    MadJson::getString(paths, mSections[i].artKey.c_str())};
                if (!path.empty())
                    mSidebar->setIcon(static_cast<int>(i), path);
            }
        });
}

void GuiMadPanel::switchSection(const int index)
{
    LOG(LogDebug) << "GuiMadPanel: section -> " << mSections[index].label;
    mCurrentSection = index;
    MadTheme::getInstance().setActivePage(mSections[index].artKey);
    refreshThemedBackground();
    mSidebar->setActive(index);
    // The sticky status belongs to the old section's pages — don't leak it.
    mFooter->setStatus("");
    mPageStack.clear();
    MadPage* root {makeRootPage(index)};
    root->setTitleHidden(true); // The sidebar already names the section.
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
    if (section.label == "Lightgun")
        return new GuiMadPageLightgun(this);
    if (section.label == "Standalones")
        return new GuiMadPageStandalones(this);
    if (section.label == "RetroArch")
        return new GuiMadPageRetroArch(this);
    if (section.label == "Bezel Project")
        return new GuiMadPageBezelProject(this);
    if (section.label == "X-Arcade")
        return new GuiMadPageXArcade(this);
    if (section.label == "Gamepads")
        return new GuiMadPageGamepads(this);
    if (section.label == "Splash")
        return new GuiMadPageSplash(this);
    if (section.label == "Backup")
        return new GuiMadPageBackup(this);
    // Unreachable: every registry entry is mapped above. Fail safe anyway.
    LOG(LogError) << "GuiMadPanel: no page for section \"" << section.label << "\"";
    return new GuiMadPageSystems(this);
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
    // Stickies are page-scoped: the parent's pending status must not cover the
    // child's help prompts. An active flash still finishes — setStatus("")
    // only replaces the saved sticky underneath it.
    mFooter->setStatus("");
    preparePage(page);
    mPageStack.emplace_back(page);
    updateHelpPrompts();
}

void GuiMadPanel::popPage()
{
    if (mPageStack.size() <= 1)
        return;
    // The dying page's sticky must not outlive it: every clear path is
    // page-owned (pageRequest callbacks are dropped, stream callbacks are
    // cleared in destructors), so an orphaned sticky would cover the help
    // prompts until the next section switch.
    mFooter->setStatus("");
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

    // A CAPTURE stream is live (Daphne/button detect): the press the daemon is
    // reading also reaches SDL; the capture modal handles its own input and the
    // panel must swallow the rest. A TESTER lock sets nav=true (the tested pad
    // is grabbed, no echo), so other pads still navigate — e.g. reach STOP.
    if (mInputLocked && !mInputLockAllowNav)
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

void GuiMadPanel::refreshThemedBackground()
{
    const std::string path {MadTheme::backgroundPath()};
    if (path != mBackgroundImagePath) {
        mBackgroundImagePath = path;
        if (!path.empty()) {
            // Crisp pixel-art scaling; stretched edge-to-edge like the
            // reference theme stretches its view backgrounds. FULL height so the
            // footer/help strip inherits the same themed squares + per-page tint
            // as the content (not just the flat frame color).
            mBackgroundImage.setLinearInterpolation(false);
            mBackgroundImage.setResize(mSize.x, mSize.y);
            mBackgroundImage.setImage(path);
            mBackgroundImage.setPosition(0.0f, 0.0f);
            mBackgroundImage.setOrigin(0.0f, 0.0f);
        }
    }
    // The tint can differ per page even when the image is shared.
    if (!mBackgroundImagePath.empty())
        mBackgroundImage.setColorShift(MadTheme::backgroundColor());
}

void GuiMadPanel::render(const glm::mat4& parentTrans)
{
    glm::mat4 trans {parentTrans * getTransform()};
    mRenderer->setMatrix(trans);

    // Fully opaque, flat menu-scheme background — the gamelist view behind the
    // panel must not show through. FULL height now (incl. the help strip) so
    // the strip shows the MAD page color, not the gamelist behind — ES-DE's own
    // help row (drawn by Window BEFORE the top GUI) is covered; we re-render our
    // own help on top below (mStripHelp).
    mRenderer->drawRect(0.0f, 0.0f, mSize.x, mSize.y, MadTheme::color(MadColor::Frame),
                        MadTheme::color(MadColor::Frame));
    // Themed background image (squares + per-page tint) on top of the opaque
    // base, FULL height — so the footer/help strip matches the page, not a flat
    // frame bar. Help/status text renders on top (same as the content area).
    if (!mBackgroundImagePath.empty())
        mBackgroundImage.render(trans);
    // Thin separator between the sidebar and the content area.
    mRenderer->drawRect(mSidebarWidth, 0.0f, std::max(1.0f, mSize.x * 0.0012f),
                        mSize.y - mHelpReserve, MadTheme::color(MadColor::Separators), MadTheme::color(MadColor::Separators));

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

    // Our own help row, on the themed strip (the footer's status, drawn in
    // renderChildren above, replaces it when present — getHelpPrompts() returns
    // empty while the footer has text). Re-feed only when the prompts change.
    const std::vector<HelpPrompt> prompts {getHelpPrompts()};
    std::string sig;
    for (const HelpPrompt& p : prompts)
        sig += p.first + "\x1f" + p.second + "\x1e";
    if (sig != mStripHelpSig) {
        mStripHelpSig = sig;
        mStripHelp.setPrompts(prompts);
    }
    if (!prompts.empty())
        mStripHelp.render(trans);
}

std::vector<HelpPrompt> GuiMadPanel::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts;

    // The footer owns the help strip while it has something to say.
    if (mPanelState == PanelState::Ready && mFooter != nullptr && mFooter->hasText())
        return prompts;

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
