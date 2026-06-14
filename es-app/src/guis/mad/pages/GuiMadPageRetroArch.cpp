//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageRetroArch.cpp
//
//  MAD control panel: global RetroArch settings (retroarch.cfg) — deck-patches.
//  Mirrors GuiMadPageModel2's GROUPS renderer; the only differences are the RPC
//  namespace (retroarch.*), the intro text and the RA-is-running warning.
//

#include "guis/mad/pages/GuiMadPageRetroArch.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"
#include "guis/mad/pages/GuiMadPageRetroArchInput.h"

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

GuiMadPageRetroArch::GuiMadPageRetroArch(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "RETROARCH"}
{
}

void GuiMadPageRetroArch::build()
{
    setLoadingText("Loading RetroArch settings…");
    pageRequest(
        "retroarch.get", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load RetroArch settings: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        },
        8000);
}

void GuiMadPageRetroArch::rebuild(const rapidjson::Value& result)
{
    beginColumn();
    addBlock("RetroArch global defaults — these apply to every core. Per-system tweaks live on "
             "the Systems page. A one-time backup (retroarch.cfg.mad-bak) is made before the "
             "first change.",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Primary),
             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);

    addButton("Keybindings / Input…",
              [this] { mPanel->pushPage(new GuiMadPageRetroArchInput(mPanel)); });

    if (MadJson::getBool(result, "running", false))
        addBlock("●  RetroArch is running — close it before changing these (it rewrites its "
                 "config on exit and would undo your changes).",
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

            // Adjacent booleans collapse into one wrapping chip row; other types
            // are steppers. Flushing before each stepper keeps the declared order.
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
            }
            flush();
        }
    }
    endColumn();
}

void GuiMadPageRetroArch::addEnumStepper(const rapidjson::Value& setting, const std::string& key,
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
    else { // enum: value is the option index
        curIdx = std::clamp(MadJson::getInt(setting, "value", 0), 0, last);
    }

    addStepper(
        label, 0.0f, static_cast<float>(last), 1.0f,
        [options, last](const float v) {
            return options[std::clamp(static_cast<int>(std::lround(v)), 0, last)];
        },
        [this, key, label, type, options, last](const float v) {
            const int i {std::clamp(static_cast<int>(std::lround(v)), 0, last)};
            // resolution → "WxH" string; enum → the index (backend maps it back).
            setOption(key, type == "resolution" ? options[i] : std::to_string(i), label);
        },
        static_cast<float>(curIdx), 0.95f);
}

void GuiMadPageRetroArch::addNumberStepper(const rapidjson::Value& setting, const std::string& key,
                                           const std::string& label, const bool isFloat)
{
    float lo {static_cast<float>(numberAt(setting, "min", 0.0))};
    float hi {static_cast<float>(numberAt(setting, "max", isFloat ? 2.5 : 9.0))};
    if (hi < lo)
        std::swap(lo, hi);
    const float step {static_cast<float>(numberAt(setting, "step", isFloat ? 0.1 : 1.0))};
    const float cur {std::clamp(static_cast<float>(numberAt(setting, "value", lo)), lo, hi)};

    addStepper(
        label, lo, hi, step,
        [isFloat](const float v) {
            char buf[24];
            if (isFloat)
                std::snprintf(buf, sizeof(buf), "%.1f", v);
            else
                std::snprintf(buf, sizeof(buf), "%d", static_cast<int>(std::lround(v)));
            return std::string {buf};
        },
        [this, key, label, isFloat](const float v) {
            char buf[24];
            if (isFloat)
                std::snprintf(buf, sizeof(buf), "%.1f", v);
            else
                std::snprintf(buf, sizeof(buf), "%d", static_cast<int>(std::lround(v)));
            setOption(key, std::string {buf}, label);
        },
        cur, 0.95f);
}

void GuiMadPageRetroArch::setOption(const std::string& key, const std::string& value,
                                    const std::string& label,
                                    const std::function<void()>& revert)
{
    pageRequest(
        "retroarch.set",
        [key, value](MadJson::Writer& writer) {
            writer.Key("key");
            writer.String(key.c_str(), static_cast<rapidjson::SizeType>(key.length()));
            writer.Key("value");
            writer.String(value.c_str(), static_cast<rapidjson::SizeType>(value.length()));
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
            footer()->flash("Saved " + label);
        });
}
