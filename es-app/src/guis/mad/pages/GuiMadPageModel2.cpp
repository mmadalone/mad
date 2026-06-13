//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  GuiMadPageModel2.cpp
//
//  MAD control panel: Sega Model 2 emulator settings (deck-patches).
//

#include "guis/mad/pages/GuiMadPageModel2.h"

#include "guis/mad/GuiMadPanel.h"
#include "guis/mad/MadFooter.h"
#include "guis/mad/MadTheme.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <map>
#include <vector>

namespace
{
    // model2.* min/max/step/value can be floats (gamma 0.5/0.1) — MadJson only
    // exposes getInt, so read numbers straight off the rapidjson value.
    double numberAt(const rapidjson::Value& obj, const char* key, const double def)
    {
        const rapidjson::Value& m {MadJson::getMember(obj, key)};
        return m.IsNumber() ? m.GetDouble() : def;
    }
} // namespace

GuiMadPageModel2::GuiMadPageModel2(GuiMadPanel* panel)
    : MadLightgunPageBase {panel, "SEGA MODEL 2"}
{
}

void GuiMadPageModel2::build()
{
    setLoadingText("Loading Model 2 settings…");
    pageRequest(
        "model2.get", nullptr,
        [this](bool ok, const rapidjson::Value& payload) {
            setLoadingText("");
            if (!ok) {
                footer()->setStatus("Couldn't load Model 2 settings: " +
                                        MadJson::getString(payload, "message", "unknown error"),
                                    true);
                return;
            }
            rebuild(payload);
        },
        8000);
}

void GuiMadPageModel2::rebuild(const rapidjson::Value& result)
{
    beginColumn();
    addBlock("ElSemi's Sega Model 2 emulator (Proton). Changes apply the next time you "
             "launch a Model 2 game.",
             FONT_SIZE_SMALL, MadTheme::color(MadColor::Primary),
             Font::get(FONT_SIZE_SMALL)->getHeight() * 0.4f);

    if (!MadJson::getBool(result, "exists", false)) {
        addBlock("○  EMULATOR.INI not found at ~/Emulation/roms/model2 — launch a Model 2 "
                 "game once to create it.",
                 FONT_SIZE_SMALL, MadTheme::color(MadColor::Red),
                 Font::get(FONT_SIZE_SMALL)->getHeight() * 0.2f);
        endColumn();
        return;
    }

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

void GuiMadPageModel2::addEnumStepper(const rapidjson::Value& setting, const std::string& key,
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
            // resolution → "WxH" string; enum → the index (backend stores str(index)).
            setOption(key, type == "resolution" ? options[i] : std::to_string(i), label);
        },
        static_cast<float>(curIdx),
        // Near-full width so the (longer) labels + values aren't ellipsized —
        // these read as label-left / ‹value›-right settings rows.
        0.95f);
}

void GuiMadPageModel2::addNumberStepper(const rapidjson::Value& setting, const std::string& key,
                                        const std::string& label, const bool isFloat)
{
    const float lo {static_cast<float>(numberAt(setting, "min", 0.0))};
    const float hi {static_cast<float>(numberAt(setting, "max", isFloat ? 2.5 : 9.0))};
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
        cur, 0.95f); // near-full width so label + value aren't ellipsized
}

void GuiMadPageModel2::setOption(const std::string& key, const std::string& value,
                                 const std::string& label,
                                 const std::function<void()>& revert)
{
    pageRequest(
        "model2.set",
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
