//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadVideoComponent.h
//
//  Tiny VideoFFmpegComponent subclass for the MAD control panel
//  (deck-patches): art-first, then a delayed video pre-roll, exactly like a
//  themed ES-DE gamelist "video" element with a <delay>. VideoComponent's own
//  ctor leaves showStaticImageDelay FALSE — it is only ever flipped on inside
//  applyTheme() when a theme <delay> is present (VideoComponent.cpp:329-330)
//  — and mConfig is protected with no public setter, so a bare
//  VideoFFmpegComponent used outside the theme system shows a black frame
//  then plays immediately. MAD has no theme to apply, so this subclass sets
//  the same two Configuration fields a themed <delay> would: showStaticImageDelay
//  true and a 1500ms startDelay (ES-DE's own default — VideoComponent.cpp:63).
//

#ifndef ES_APP_GUIS_MAD_WIDGETS_MAD_VIDEO_COMPONENT_H
#define ES_APP_GUIS_MAD_WIDGETS_MAD_VIDEO_COMPONENT_H

#include "components/VideoFFmpegComponent.h"

class MadVideoComponent : public VideoFFmpegComponent
{
public:
    MadVideoComponent()
    {
        // mConfig is a protected VideoComponent member — reachable from a
        // derived class even though its Configuration struct type is private.
        mConfig.showStaticImageDelay = true;
        mConfig.startDelay = 1500;
    }
};

#endif // ES_APP_GUIS_MAD_WIDGETS_MAD_VIDEO_COMPONENT_H
