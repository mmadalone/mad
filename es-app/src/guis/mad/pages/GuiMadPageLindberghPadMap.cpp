//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageLindberghPadMap.cpp  (deck-patches)
//

#include "guis/mad/pages/GuiMadPageLindberghPadMap.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

GuiMadPageLindberghPadMap::GuiMadPageLindberghPadMap(GuiMadPanel* panel, const std::string& title,
                                                     const std::string& titleid,
                                                     const std::string& tag,
                                                     const std::string& padName)
    : MadLightgunPageBase {panel, title}
    , mTitleId {titleid}
    , mTag {tag}
    , mPadName {padName}
{
}

void GuiMadPageLindberghPadMap::build()
{
    setLoadingText("Loading…");
    load();
}

void GuiMadPageLindberghPadMap::load()
{
    const std::string tid {mTitleId};
    const std::string tag {mTag};
    pageRequest(
        "lindbergh.pad_load",
        [tid, tag](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(tid.c_str(), static_cast<rapidjson::SizeType>(tid.length()));
            writer.Key("tag");
            writer.String(tag.c_str(), static_cast<rapidjson::SizeType>(tag.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            parse(payload);
            relayout();
        },
        10000);
}

void GuiMadPageLindberghPadMap::parse(const rapidjson::Value& result)
{
    mCaption = MadJson::getString(result, "caption");
    mSections.clear();
    const rapidjson::Value& sections {MadJson::getMember(result, "sections")};
    if (sections.IsObject())
        for (auto it = sections.MemberBegin(); it != sections.MemberEnd(); ++it) {
            std::vector<std::string> keys;
            if (it->value.IsArray())
                for (rapidjson::SizeType i {0}; i < it->value.Size(); ++i)
                    if (it->value[i].IsString())
                        keys.emplace_back(it->value[i].GetString());
            mSections[it->name.GetString()] = keys;
        }

    mRows.clear();
    const rapidjson::Value& rows {MadJson::getMember(result, "rows")};
    if (rows.IsObject())
        for (auto it = rows.MemberBegin(); it != rows.MemberEnd(); ++it) {
            Row row;
            row.key = MadJson::getString(it->value, "key");
            row.label = MadJson::getString(it->value, "label", row.key);
            row.display = MadJson::getString(it->value, "display");
            row.kind = MadJson::getString(it->value, "kind");
            row.warn = MadJson::getBool(it->value, "warn");
            row.axis = MadJson::getBool(it->value, "axis");
            if (!row.key.empty())
                mRows[row.key] = row;
        }
}

std::string GuiMadPageLindberghPadMap::rowText(const Row& row) const
{
    return row.label + " — " + row.display + (row.warn ? "  ⚠" : "");
}

void GuiMadPageLindberghPadMap::applyRowUpdate(const rapidjson::Value& row)
{
    const std::string key {MadJson::getString(row, "key")};
    if (key.empty())
        return;
    Row r;
    r.key = key;
    r.label = MadJson::getString(row, "label", key);
    r.display = MadJson::getString(row, "display");
    r.kind = MadJson::getString(row, "kind");
    r.warn = MadJson::getBool(row, "warn");
    r.axis = MadJson::getBool(row, "axis");
    mRows[key] = r;
}

void GuiMadPageLindberghPadMap::relayout()
{
    mControlActions.clear();
    beginColumn();
    if (!mCaption.empty())
        caption(mCaption);

    // One headed group per family, in a fixed order (the section map's key order is irrelevant).
    auto addGroup = [this](const std::string& title, const std::string& section) {
        const auto sec = mSections.find(section);
        if (sec == mSections.end() || sec->second.empty())
            return;
        header(title);
        if (section == "analog")
            caption("Bind by MOVING the control (stick / wheel / pedal / trigger).");
        for (const std::string& ctrl : sec->second) {
            const auto it = mRows.find(ctrl);
            if (it == mRows.end())
                continue;
            addButton(rowText(it->second), [this, ctrl] { bindControl(ctrl); });
            mControlActions.resize(mControls.size(), std::string {});
            mControlActions.back() = ctrl; // the just-added control maps to this key
        }
    };

    addGroup("Buttons", "buttons");
    addGroup("D-pad (directions)", "dpad");
    addGroup("Analog (sticks / pedals / wheel)", "analog");
    addGroup("System", "system");

    endColumn();
    mControlActions.resize(mControls.size(), std::string {});
}

void GuiMadPageLindberghPadMap::bindControl(const std::string& key)
{
    if (mBinding)
        return;
    mBinding = true;
    const auto it = mRows.find(key);
    const std::string label {it != mRows.end() ? it->second.label : key};
    const std::string kind {it != mRows.end() ? it->second.kind : std::string {}};
    // The capture mode is decided server-side from the control key; the status text just matches it.
    std::string verb {"Press "};
    if (kind == "analog")
        verb = "Move ";
    else if (kind == "direction")
        // a direction key takes a D-pad, a stick push, OR a button/paddle (gear shift / boost)
        verb = "Press or push the control for ";
    footer()->setStatus(verb + label + " on " + mPadName + " now… (10s)");
    const std::string tid {mTitleId};
    const std::string tag {mTag};
    pageRequest(
        "lindbergh.pad_bind",
        [tid, tag, key, label](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(tid.c_str(), static_cast<rapidjson::SizeType>(tid.length()));
            writer.Key("tag");
            writer.String(tag.c_str(), static_cast<rapidjson::SizeType>(tag.length()));
            writer.Key("control");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("label");
            writer.String(label.c_str(), static_cast<rapidjson::SizeType>(label.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            mBinding = false;
            footer()->setStatus("");
            if (!ok) {
                footer()->flash("Bind failed: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            const rapidjson::Value& rows {MadJson::getMember(payload, "rows")};
            bool changed {false};
            if (rows.IsObject())
                for (auto r = rows.MemberBegin(); r != rows.MemberEnd(); ++r) {
                    applyRowUpdate(r->value);
                    changed = true;
                }
            if (changed)
                relayout();
            footer()->flash(MadJson::getString(payload, "message"),
                            5000, MadJson::getBool(payload, "warn"));
        },
        20000); // the capture runs up to ~14 s in the daemon.
}

void GuiMadPageLindberghPadMap::clearControl(const std::string& key)
{
    const auto it = mRows.find(key);
    const std::string label {it != mRows.end() ? it->second.label : key};
    const std::string tid {mTitleId};
    const std::string tag {mTag};
    pageRequest(
        "lindbergh.pad_clear",
        [tid, tag, key, label](MadJson::Writer& writer) {
            writer.Key("titleid");
            writer.String(tid.c_str(), static_cast<rapidjson::SizeType>(tid.length()));
            writer.Key("tag");
            writer.String(tag.c_str(), static_cast<rapidjson::SizeType>(tag.length()));
            writer.Key("control");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("label");
            writer.String(label.c_str(), static_cast<rapidjson::SizeType>(label.length()));
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash(MadJson::getString(payload, "message", "unknown error"), 4000, true);
                return;
            }
            applyRowUpdate(MadJson::getMember(payload, "row"));
            relayout();
            footer()->flash(MadJson::getString(payload, "message"));
        });
}

bool GuiMadPageLindberghPadMap::input(InputConfig* config, Input input)
{
    if (input.value != 0 && config->isMappedTo("start", input) && mBuilt &&
        mFocus < static_cast<int>(mControlActions.size()) && !mControlActions[mFocus].empty()) {
        if (!mBinding)
            clearControl(mControlActions[mFocus]);
        return true;
    }
    return MadLightgunPageBase::input(config, input);
}

std::vector<HelpPrompt> GuiMadPageLindberghPadMap::getHelpPrompts()
{
    std::vector<HelpPrompt> prompts {MadLightgunPageBase::getHelpPrompts()};
    if (mBuilt && mFocus < static_cast<int>(mControlActions.size()) &&
        !mControlActions[mFocus].empty()) {
        const auto it = mRows.find(mControlActions[mFocus]);
        const bool axis {it != mRows.end() && it->second.axis};
        for (HelpPrompt& prompt : prompts)
            if (prompt.first == "a")
                prompt.second = axis ? "move to bind" : "bind";
        prompts.push_back(HelpPrompt("start", "clear"));
    }
    return prompts;
}
