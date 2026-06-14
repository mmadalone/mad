//  SPDX-License-Identifier: MIT
//
//  ES-DE Frontend
//  MadJson.h
//
//  Thin RapidJSON helpers for the MAD backend NDJSON protocol (deck-patches).
//

#ifndef ES_APP_GUIS_MAD_MAD_JSON_H
#define ES_APP_GUIS_MAD_MAD_JSON_H

#include "rapidjson/document.h"
#include "rapidjson/stringbuffer.h"
#include "rapidjson/writer.h"

#include <functional>
#include <string>

namespace MadJson
{
    using Writer = rapidjson::Writer<rapidjson::StringBuffer>;
    // Writes the members of a request's "params" object (nullptr means no params).
    using ParamsWriter = std::function<void(Writer&)>;

    inline bool parseLine(const std::string& line, rapidjson::Document& doc)
    {
        doc.Parse<rapidjson::kParseIterativeFlag>(line.c_str(), line.length());
        return !doc.HasParseError() && doc.IsObject();
    }

    // Shared null sentinel returned by getMember() for missing/invalid members so
    // callers can chain lookups without null checks.
    inline const rapidjson::Value& nullValue()
    {
        static const rapidjson::Value sNull;
        return sNull;
    }

    inline const rapidjson::Value& getMember(const rapidjson::Value& obj, const char* key)
    {
        if (!obj.IsObject())
            return nullValue();
        const auto it = obj.FindMember(key);
        if (it == obj.MemberEnd())
            return nullValue();
        return it->value;
    }

    inline std::string getString(const rapidjson::Value& obj,
                                 const char* key,
                                 const std::string& defaultValue = "")
    {
        const rapidjson::Value& member {getMember(obj, key)};
        if (!member.IsString())
            return defaultValue;
        return std::string(member.GetString(), member.GetStringLength());
    }

    inline bool getBool(const rapidjson::Value& obj, const char* key, bool defaultValue = false)
    {
        const rapidjson::Value& member {getMember(obj, key)};
        if (!member.IsBool())
            return defaultValue;
        return member.GetBool();
    }

    inline int getInt(const rapidjson::Value& obj, const char* key, int defaultValue = 0)
    {
        const rapidjson::Value& member {getMember(obj, key)};
        if (member.IsInt())
            return member.GetInt();
        if (member.IsNumber())
            return static_cast<int>(member.GetDouble());
        return defaultValue;
    }

    // Builds {"id":N,"method":"...","params":{...}} as a single line (no trailing newline).
    inline std::string makeRequest(const int id,
                                   const std::string& method,
                                   const ParamsWriter& params = nullptr)
    {
        rapidjson::StringBuffer buffer;
        Writer writer {buffer};
        writer.StartObject();
        writer.Key("id");
        writer.Int(id);
        writer.Key("method");
        writer.String(method.c_str(), static_cast<rapidjson::SizeType>(method.length()));
        writer.Key("params");
        writer.StartObject();
        if (params)
            params(writer);
        writer.EndObject();
        writer.EndObject();
        return std::string(buffer.GetString(), buffer.GetSize());
    }
} // namespace MadJson

#endif // ES_APP_GUIS_MAD_MAD_JSON_H
