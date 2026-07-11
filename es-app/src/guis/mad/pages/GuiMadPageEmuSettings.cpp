//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageEmuSettings.cpp
//
//  MAD control panel: generic GROUPS-driven settings page (deck-patches). Same
//  renderer as GuiMadPageModel2 / GuiMadPageRetroArch, parameterised by RPC
//  namespace so every standalone emulator's Settings section reuses it.
//

#include "guis/mad/pages/GuiMadPageEmuSettings.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageBackends.h" // GuiMadPageBackendChoice (long-option picker)

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <utility>
#include <vector>

namespace
{
    double numberAt(const rapidjson::Value& obj, const char* key, const double def)
    {
        const rapidjson::Value& m {MadJson::getMember(obj, key)};
        return m.IsNumber() ? m.GetDouble() : def;
    }
} // namespace

GuiMadPageEmuSettings::GuiMadPageEmuSettings(GuiMadPanel* panel, const std::string& title,
                                             const std::string& ns, const std::string& ctxKey,
                                             const std::string& ctxVal, const std::string& core)
    : MadLightgunPageBase {panel, title}
    , mNs {ns}
    , mCtxKey {ctxKey}
    , mCtxVal {ctxVal}
    , mCore {core}
{
}

void GuiMadPageEmuSettings::build()
{
    setLoadingText("Loading settings…");
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string core {mCore};
    pageRequest(
        mNs + ".get",
        [ctxKey, ctxVal, core](MadJson::Writer& w) {
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!core.empty()) {
                w.Key("core");
                w.String(core.c_str(), static_cast<rapidjson::SizeType>(core.length()));
            }
        },
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load settings: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        },
        8000);
}

void GuiMadPageEmuSettings::rebuild(const rapidjson::Value& result)
{
    beginColumn();

    mBuffered = MadJson::getBool(result, "buffered", false);
    mDirty = MadJson::getBool(result, "dirty", false);
    const std::string note {MadJson::getString(
        result, "note",
        "Changes save instantly; a one-time backup is made before the first change.")};
    addBlock(note, FONT_SIZE_SMALL, MadTheme::color(MadColor::Primary),
             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);

    if (!MadJson::getBool(result, "exists", true)) {
        addBlock("○  " + MadJson::getString(result, "note",
                     "Config file not found — launch a game once to create it."),
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Red),
                 Font::get(FONT_SIZE_SMALL)->getHeight() * 0.2f);
        endColumn();
        return;
    }
    if (MadJson::getBool(result, "running", false))
        addBlock("●  The emulator is running — close it before changing these (it rewrites "
                 "its config on exit and would undo your changes).",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Red),
                 Font::get(FONT_SIZE_SMALL)->getHeight() * 0.2f);

    const rapidjson::Value& groups {MadJson::getMember(result, "groups")};
    if (groups.IsArray()) {
        for (const rapidjson::Value& g : groups.GetArray()) {
            header(MadJson::getString(g, "title"));
            caption(MadJson::getString(g, "note"));

            const rapidjson::Value& settings {MadJson::getMember(g, "settings")};
            if (!settings.IsArray())
                continue;

            std::vector<MadChipRow::Chip> pendingBools;
            auto flush = [this, &pendingBools]() {
                if (pendingBools.empty())
                    return;
                std::map<std::string, std::string> labels;
                for (const MadChipRow::Chip& c : pendingBools)
                    labels[c.value] = c.label;
                auto row = addChips(pendingBools, false);
                MadChipRow* raw {row.get()};
                row->setOnToggle([this, raw, labels](const std::string& key, bool on) {
                    const auto it = labels.find(key);
                    const std::string lbl {it != labels.end() ? it->second : key};
                    setOption(key, on ? "1" : "0", lbl,
                              [raw, key, on] { raw->setChipState(key, !on); });
                });
                pendingBools.clear();
            };

            for (const rapidjson::Value& s : settings.GetArray()) {
                const std::string type {MadJson::getString(s, "type")};
                const std::string key {MadJson::getString(s, "key")};
                const std::string label {MadJson::getString(s, "label", key)};
                if (type == "bool") {
                    pendingBools.push_back({key, label, MadJson::getBool(s, "value")});
                    continue;
                }
                flush();
                if (type == "enum" || type == "resolution")
                    addEnumStepper(s, key, label, type);
                else if (type == "int")
                    addNumberStepper(s, key, label, false);
                else if (type == "float")
                    addNumberStepper(s, key, label, true);
                else if (type == "action")
                    addActionButton(s, label);
            }
            flush();
        }
    }
    endColumn();
}

namespace
{
    // A long or large option list opens the scrollable picker on A; short ones stay a quick ‹ ›
    // stepper (no extra press for Off/On). Tuned so the pack option lists (50+ resolutions, long
    // preset names) and the 12-entry console-language list get the picker.
    bool useOptionPicker(const std::vector<std::string>& options)
    {
        if (options.size() > 8)
            return true;
        for (const std::string& o : options)
            if (o.length() > 22)
                return true;
        return false;
    }
} // namespace

void GuiMadPageEmuSettings::addEnumStepper(const rapidjson::Value& setting, const std::string& key,
                                           const std::string& label, const std::string& type)
{
    std::vector<std::string> options;
    const rapidjson::Value& opts {MadJson::getMember(setting, "options")};
    if (opts.IsArray())
        for (const rapidjson::Value& o : opts.GetArray())
            if (o.IsString())
                options.emplace_back(o.GetString(), o.GetStringLength());
    if (options.empty())
        return;

    const int last {static_cast<int>(options.size()) - 1};
    int curIdx {0};
    if (type == "resolution") {
        const std::string cur {MadJson::getString(setting, "value")};
        for (size_t i {0}; i < options.size(); ++i)
            if (options[i] == cur) {
                curIdx = static_cast<int>(i);
                break;
            }
    }
    else {
        curIdx = std::clamp(MadJson::getInt(setting, "value", 0), 0, last);
    }

    // byText: the backend stores the option TEXT for a "resolution", the option INDEX for an enum.
    const bool byText {type == "resolution"};

    auto stepper = addStepper(
        label, 0.0f, static_cast<float>(last), 1.0f,
        [options, last](const float v) {
            return options[std::clamp(static_cast<int>(std::lround(v)), 0, last)];
        },
        [this, key, label, byText, options, last](const float v) {
            const int i {std::clamp(static_cast<int>(std::lround(v)), 0, last)};
            setOption(key, byText ? options[i] : std::to_string(i), label);
        },
        static_cast<float>(curIdx), 0.95f);

    // Long / large lists: pressing A opens the shared scrollable picker (full names, scroll instead
    // of cycle). The stepper still shows the current value + cycles with left/right for small nudges.
    // A setting may also FORCE the picker regardless of option count ("picker": true) -- e.g. the
    // On-the-go resolution rows, so A always opens the full list (WS-H).
    const bool forcePicker {MadJson::getBool(setting, "picker", false)};
    if (!forcePicker && !useOptionPicker(options))
        return;
    std::weak_ptr<MadStepper> weak {stepper};
    stepper->setOnActivate([this, key, label, byText, options, last, weak] {
        auto s {weak.lock()};
        if (s == nullptr)
            return;
        std::vector<std::pair<std::string, std::string>> choices; // (stored value, display label)
        for (int i {0}; i <= last; ++i)
            choices.emplace_back(byText ? options[i] : std::to_string(i), options[i]);
        const int cur {std::clamp(static_cast<int>(std::lround(s->value())), 0, last)};
        mPanel->pushPage(new GuiMadPageBackendChoice(
            mPanel, label, "", choices, byText ? options[cur] : std::to_string(cur),
            [this, key, label, byText, options, last, weak](const std::string& value) {
                int i {0};
                for (int j {0}; j <= last; ++j)
                    if ((byText ? options[j] : std::to_string(j)) == value) {
                        i = j;
                        break;
                    }
                if (auto sp {weak.lock()})
                    sp->setValue(static_cast<float>(i)); // update the row display in place
                setOption(key, byText ? options[i] : std::to_string(i), label); // setValue doesn't write
            }));
    });
}

void GuiMadPageEmuSettings::addNumberStepper(const rapidjson::Value& setting, const std::string& key,
                                             const std::string& label, const bool isFloat)
{
    float lo {static_cast<float>(numberAt(setting, "min", 0.0))};
    float hi {static_cast<float>(numberAt(setting, "max", isFloat ? 2.5 : 9.0))};
    if (hi < lo)
        std::swap(lo, hi);
    const float step {static_cast<float>(numberAt(setting, "step", isFloat ? 0.1 : 1.0))};
    // Per-game numeric settings gain an "Inherit global" slot ONE STEP BELOW min: the backend
    // sends inherit:true (+ inherited: whether it's currently inheriting). Selecting that slot
    // sends the "inherit" sentinel, which the backend maps to clearing the per-game override.
    const bool inherit {MadJson::getBool(setting, "inherit", false)};
    const bool inherited {inherit && MadJson::getBool(setting, "inherited", false)};
    const float loEff {inherit ? lo - step : lo};
    const float threshold {lo - step * 0.5f}; // a stepper value below this == the inherit slot
    const float cur {inherited
                         ? loEff
                         : std::clamp(static_cast<float>(numberAt(setting, "value", lo)), lo, hi)};

    addStepper(
        label, loEff, hi, step,
        [isFloat, inherit, threshold](const float v) {
            if (inherit && v < threshold)
                return std::string {"Inherit global"};
            char buf[24];
            if (isFloat)
                std::snprintf(buf, sizeof(buf), "%.1f", v);
            else
                std::snprintf(buf, sizeof(buf), "%d", static_cast<int>(std::lround(v)));
            return std::string {buf};
        },
        [this, key, label, isFloat, inherit, threshold](const float v) {
            if (inherit && v < threshold) {
                setOption(key, std::string {"inherit"}, label);
                return;
            }
            char buf[24];
            if (isFloat)
                std::snprintf(buf, sizeof(buf), "%.1f", v);
            else
                std::snprintf(buf, sizeof(buf), "%d", static_cast<int>(std::lround(v)));
            setOption(key, std::string {buf}, label);
        },
        cur, 0.95f);
}

void GuiMadPageEmuSettings::addActionButton(const rapidjson::Value& setting,
                                            const std::string& label)
{
    const std::string rpc {MadJson::getString(setting, "rpc")};
    if (rpc.empty())
        return;
    // Snapshot the args object (string values only) into owned strings — the
    // rapidjson Value isn't safe to hold past build(). Same RPC-fire pattern as
    // GuiMadPageLightgun::driverAction.
    std::vector<std::pair<std::string, std::string>> args;
    const rapidjson::Value& a {MadJson::getMember(setting, "args")};
    if (a.IsObject())
        for (auto it = a.MemberBegin(); it != a.MemberEnd(); ++it)
            if (it->value.IsString())
                args.emplace_back(it->name.GetString(),
                                  std::string {it->value.GetString(), it->value.GetStringLength()});

    addButton(label, [this, rpc, args] {
        pageRequest(
            rpc,
            [args](MadJson::Writer& writer) {
                for (const std::pair<std::string, std::string>& kv : args) {
                    writer.Key(kv.first.c_str());
                    writer.String(kv.second.c_str(),
                                  static_cast<rapidjson::SizeType>(kv.second.length()));
                }
            },
            [this](bool ok, const rapidjson::Value& payload) {
                footer()->setStatus("");
                footer()->flash(MadJson::getString(payload, "message", "unknown error"), 5000,
                                !ok);
            },
            10000);
    });
}

void GuiMadPageEmuSettings::setOption(const std::string& key, const std::string& value,
                                      const std::string& label,
                                      const std::function<void()>& revert)
{
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string core {mCore};
    pageRequest(
        mNs + ".set",
        [key, value, ctxKey, ctxVal, core](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("value");
            writer.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
            if (!ctxKey.empty()) {
                writer.Key(ctxKey.c_str());
                writer.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!core.empty()) {
                writer.Key("core");
                writer.String(core.c_str(), static_cast<rapidjson::SizeType>(core.length()));
            }
        },
        [this, label, revert](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't save " + label + ": " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                if (revert)
                    revert();
                return;
            }
            if (mBuffered)
                mDirty = MadJson::getBool(payload, "dirty", true);
            footer()->flash(mBuffered ? label + ", press X to save" : "Saved " + label);
        });
}

void GuiMadPageEmuSettings::requestSave()
{
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string core {mCore};
    pageRequest(
        mNs + ".save",
        [ctxKey, ctxVal, core](MadJson::Writer& w) {
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!core.empty()) {
                w.Key("core");
                w.String(core.c_str(), static_cast<rapidjson::SizeType>(core.length()));
            }
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (ok)
                mDirty = false;
            footer()->flash(MadJson::getString(payload, "message", ok ? "Saved." : "Save failed"),
                            4000, !ok);
        },
        8000);
}

void GuiMadPageEmuSettings::requestCancel()
{
    const std::string ctxKey {mCtxKey};
    const std::string ctxVal {mCtxVal};
    const std::string core {mCore};
    pageRequest(
        mNs + ".cancel",
        [ctxKey, ctxVal, core](MadJson::Writer& w) {
            if (!ctxKey.empty()) {
                w.Key(ctxKey.c_str());
                w.String(ctxVal.c_str(), static_cast<rapidjson::SizeType>(ctxVal.length()));
            }
            if (!core.empty()) {
                w.Key("core");
                w.String(core.c_str(), static_cast<rapidjson::SizeType>(core.length()));
            }
        },
        [this](bool ok, const rapidjson::Value& payload) {
            if (!ok) {
                footer()->flash("Couldn't revert: " +
                                    MadJson::getString(payload, "message", "unknown error"),
                                4000, true);
                return;
            }
            build(); // re-get: the backend reverted its buffer, so this shows saved values
            footer()->flash(MadJson::getString(payload, "message", "Reverted to saved."));
        },
        8000);
}

bool GuiMadPageEmuSettings::madSave()
{
    if (mBuffered && mDirty) {
        requestSave();
        return true;
    }
    return false;
}

bool GuiMadPageEmuSettings::madCancel()
{
    if (mBuffered && mDirty) {
        requestCancel();
        return true;
    }
    return false;
}

std::vector<HelpPrompt> GuiMadPageEmuSettings::getHelpPrompts()
{
    auto prompts = MadLightgunPageBase::getHelpPrompts();
    if (mBuffered && mDirty) {
        prompts.push_back(HelpPrompt("x", "save"));
        prompts.push_back(HelpPrompt("y", "cancel"));
    }
    return prompts;
}
