//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLindberghPads.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageLindberghPads.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageLindberghPadMap.h"

GuiMadPageLindberghPads::GuiMadPageLindberghPads(GuiMadPanel* panel, const std::string& title,
                                                 const std::string& titleid)
    : MadLightgunPageBase {panel, title}
    , mTitleId {titleid}
{
}

void GuiMadPageLindberghPads::build()
{
    setLoadingText("Loading controllers…");
    load();
}

void GuiMadPageLindberghPads::onChildPopped()
{
    load(); // a pad map edit or reorder may have changed mapped/order — reload from truth
}

void GuiMadPageLindberghPads::load()
{
    const std::string tid {mTitleId};
    pageRequest(
        "lindbergh.pads_get",
        [tid](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(tid.c_str(), static_cast<rapidjson::SizeType>(tid.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load controllers: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            parse(payload);
            relayout();
        },
        10000);
}

void GuiMadPageLindberghPads::parse(const rapidjson::Value& result)
{
    mCaption = MadJson::getString(result, "caption");
    mPlayers = MadJson::getInt(result, "players", 2);
    mPads.clear();
    const rapidjson::Value& pads {MadJson::getMember(result, "pads")};
    if (pads.IsArray())
        for (rapidjson::SizeType i {0}; i < pads.Size(); ++i)
            mPads.push_back({MadJson::getString(pads[i], "tag"),
                             MadJson::getString(pads[i], "label", MadJson::getString(pads[i], "tag")),
                             MadJson::getBool(pads[i], "connected"),
                             MadJson::getBool(pads[i], "mapped")});
}

void GuiMadPageLindberghPads::relayout()
{
    const float smallHeight {Font::get(FONT_SIZE_SMALL)->getHeight()};
    beginColumn();

    if (!mCaption.empty())
        caption(mCaption);

    if (mPads.empty()) {
        addBlock("No controllers detected. Connect a pad or the X-Arcade, then reopen this page.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Secondary), smallHeight * 0.3f);
        endColumn();
        return;
    }

    header("Controllers — top is Player 1");
    int playerSlot {0}; // the slot a pad gets at launch = its rank among CONNECTED+MAPPED pads
    for (size_t i {0}; i < mPads.size(); ++i) {
        const Pad p {mPads[i]};
        std::string slot;
        if (p.connected && p.mapped && playerSlot < mPlayers)
            slot = "  [P" + std::to_string(++playerSlot) + "]"; // mirrors lindbergh_pads.resolve()
        const std::string flags {std::string(p.connected ? "  ●" : "  (off)") +
                                 (p.mapped ? "  ✓ mapped" : "  — not mapped")};
        const std::string tag {p.tag};
        const std::string name {p.label};
        std::vector<std::pair<std::string, std::function<void()>>> row;
        row.emplace_back("Map  " + name + slot + flags, [this, tag, name] {
            mPanel->pushPage(
                new GuiMadPageLindberghPadMap(mPanel, name + " — Buttons", mTitleId, tag, name));
        });
        if (i > 0)
            row.emplace_back("Make Player 1", [this, tag] { promote(tag); });
        addButtonRow(row, false);
    }
    endColumn();
}

void GuiMadPageLindberghPads::promote(const std::string& tag)
{
    std::vector<std::string> order {tag};
    for (const Pad& p : mPads)
        if (p.tag != tag)
            order.push_back(p.tag);
    const std::string tid {mTitleId};
    pageRequest(
        "lindbergh.pads_set_order",
        [tid, order](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(tid.c_str(), static_cast<rapidjson::SizeType>(tid.length()));
            writer.Key("order");
            writer.StartArray();
            for (const std::string& t : order)
                writer.String(t.c_str(), static_cast<rapidjson::SizeType>(t.length()));
            writer.EndArray();
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "couldn't reorder"), 4000, true);
                return;
            }
            footer()->flash("Player order updated.");
            load();
        },
        8000);
}

std::vector<HelpPrompt> GuiMadPageLindberghPads::getHelpPrompts()
{
    // Delegate to the base: it advertises up/down + left/right (so "Make Player 1" is discoverable)
    // and the panel appends "b back" globally — don't duplicate it.
    return MadLightgunPageBase::getHelpPrompts();
}
