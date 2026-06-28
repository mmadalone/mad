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
    mOrder.clear();
    const rapidjson::Value& controls {MadJson::getMember(result, "controls")};
    if (controls.IsArray())
        for (rapidjson::SizeType i {0}; i < controls.Size(); ++i)
            if (controls[i].IsString())
                mOrder.emplace_back(controls[i].GetString());

    mRows.clear();
    const rapidjson::Value& rows {MadJson::getMember(result, "rows")};
    if (rows.IsObject())
        for (auto it = rows.MemberBegin(); it != rows.MemberEnd(); ++it) {
            Row row;
            row.key = MadJson::getString(it->value, "key");
            row.label = MadJson::getString(it->value, "label", row.key);
            row.display = MadJson::getString(it->value, "display");
            row.warn = MadJson::getBool(it->value, "warn");
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
    r.warn = MadJson::getBool(row, "warn");
    mRows[key] = r;
}

void GuiMadPageLindberghPadMap::relayout()
{
    mControlActions.clear();
    beginColumn();
    if (!mCaption.empty())
        caption(mCaption);
    for (const std::string& key : mOrder) {
        const auto it = mRows.find(key);
        if (it == mRows.end())
            continue;
        addButton(rowText(it->second), [this, key] { bindControl(key); });
        mControlActions.resize(mControls.size(), std::string {});
        mControlActions.back() = key; // the just-added control maps to this key
    }
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
    footer()->setStatus("Press " + label + " on " + mPadName + " now… (10s)");
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
    if (input.value != 0 && config->isMappedTo("x", input) && mBuilt &&
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
        for (HelpPrompt& prompt : prompts)
            if (prompt.first == "a")
                prompt.second = "bind";
        prompts.push_back(HelpPrompt("x", "clear"));
    }
    return prompts;
}
