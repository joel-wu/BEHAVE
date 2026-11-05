// Stable compiler rule names, not rendered diagnostic text, cross the boundary.
#pragma once

#include <fstream>
#include "slang/diagnostics/DiagnosticClient.h"
#include "slang/text/Json.h"
#include "slang/text/SourceManager.h"

namespace behave {
class SourceDiagnostics : public slang::DiagnosticClient {
    slang::JsonWriter json;
    std::string path;
public:
    explicit SourceDiagnostics(std::string path) : path(std::move(path)) {
        json.startArray();
    }
    void report(const slang::ReportedDiagnostic& diag) override {
        json.startObject();
        json.writeProperty("code");
        json.writeValue(slang::toString(diag.originalDiagnostic.code));
        json.writeProperty("severity");
        json.writeValue(getSeverityString(diag.severity));
        json.writeProperty("message");
        json.writeValue(diag.formattedMessage);
        if (diag.location != slang::SourceLocation::NoLocation) {
            json.writeProperty("location");
            json.writeValue(getFileName(diag.location) + ":" +
                std::to_string(sourceManager->getLineNumber(diag.location)) + ":" +
                std::to_string(getColumnNumber(diag.location)));
        }
        auto& original = diag.originalDiagnostic;
        if (original.symbol && engine->getSymbolPathCB()) {
            json.writeProperty("symbolPath");
            json.writeValue(engine->getSymbolPathCB()(*original.symbol));
        }
        json.endObject();
    }
    void write() {
        json.endArray();
        std::ofstream output(path);
        json.flushTo(output);
    }
};
} // namespace behave
