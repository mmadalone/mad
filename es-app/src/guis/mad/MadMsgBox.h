//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadMsgBox.h
//
//  GuiMsgBox that swallows ALL keyboard input (deck-patches): the Sinden
//  driver synthesizes display-server keystrokes from gun presses — a raw
//  GuiMsgBox would let a gun trigger confirm a destructive dialog (the panel
//  itself already swallows the keyboard globally; this extends the rule to
//  the window-topmost confirms MAD pushes).
//

#ifndef ES_APP_GUIS_MAD_MAD_MSG_BOX_H
#define ES_APP_GUIS_MAD_MAD_MSG_BOX_H

#include "guis/GuiMsgBox.h"
#include "guis/mad/GuiMadPanel.h"
#include "renderers/Renderer.h"

class MadMsgBox : public GuiMsgBox
{
public:
    using GuiMsgBox::GuiMsgBox;

    bool input(InputConfig* config, Input input) override
    {
        if (input.device == DEVICE_KEYBOARD)
            return true; // Tk parity: the keyboard never activates MAD UI.
        return GuiMsgBox::input(config, input);
    }

    void render(const glm::mat4& parentTrans) override
    {
        // ES-DE's Window renders only the front (gamelist) + top (this dialog) GUIs, skipping the mid-stack
        // MAD panel - so a bare confirm would float over the blurred gamelist. Render the live panel (its
        // current page, opaque) first, then a light dim, then the box (GuiMsgBox has no render() of its own,
        // so GuiComponent::render draws the frame + buttons). Same proven backdrop pattern as GuiMadCaptureModal.
        if (GuiMadPanel* panel = GuiMadPanel::currentPanel())
            panel->render(parentTrans);
        Renderer* r {Renderer::getInstance()};
        r->setMatrix(parentTrans);
        r->drawRect(0.0f, 0.0f, Renderer::getScreenWidth(), Renderer::getScreenHeight(),
                    0x00000060, 0x00000060); // a light dim so the box stands out over the opaque page
        GuiComponent::render(parentTrans);
    }
};

#endif // ES_APP_GUIS_MAD_MAD_MSG_BOX_H
