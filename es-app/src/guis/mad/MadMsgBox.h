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
};

#endif // ES_APP_GUIS_MAD_MAD_MSG_BOX_H
