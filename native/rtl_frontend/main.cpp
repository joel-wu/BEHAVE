// Slang-owned source dependencies for the shared native observation plan.
#include <algorithm>
#include <map>
#include <set>
#include <fstream>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <tuple>
#include <vector>

#include "slang/analysis/AnalysisManager.h"
#include "slang/analysis/DataFlowAnalysis.h"
#include "slang/ast/Compilation.h"
#include "slang/ast/ASTSerializer.h"
#include "slang/ast/ASTVisitor.h"
#include "slang/ast/statements/LoopStatements.h"
#include "slang/ast/symbols/BlockSymbols.h"
#include "slang/ast/symbols/CompilationUnitSymbols.h"
#include "slang/ast/symbols/ParameterSymbols.h"
#include "slang/ast/types/AllTypes.h"
#include "slang/driver/Driver.h"
#include "slang/syntax/SyntaxNode.h"
#include "slang/syntax/CSTSerializer.h"
#include "slang/syntax/SyntaxTree.h"
#include "slang/text/Json.h"
#include "source_design.h"
#include "source_writes.h"
#include "source_event_controls.h"
#include "source_task_inputs.h"
#include "source_diagnostics.h"

using namespace slang;

struct ProcessReads {
    const ast::Symbol* symbol;
    analysis::SensitivityList::Kind kind;
    std::vector<analysis::ReadRange> reads;
    std::vector<const ast::CallExpression*> calls;
};

// Slang visits a net declaration initializer as an expression, not as an
// AnalyzedProcedure. Reuse its continuous-expression DFA and sensitivity rules
// without inserting a driver into the design or reparsing rewritten source.
// The temporary analysis root has the original scope; exported coordinates and
// read symbols still belong to the original net and its bound initializer.
struct SourceProcedureReads : behave::SourceDesignVisitor<SourceProcedureReads> {
    analysis::AnalysisContext context;
    std::vector<ProcessReads>& procedures;
    std::set<const ast::Symbol*> analyzed;

    SourceProcedureReads(analysis::AnalysisManager& manager, std::vector<ProcessReads>& procedures) :
        context(manager), procedures(procedures) {
        for (auto& procedure : procedures)
            analyzed.insert(procedure.symbol);
    }

    void add(const ast::Symbol& symbol) {
        if (!analyzed.insert(&symbol).second)
            return;
        // AnalysisManager reuses canonical instances even when all bodies were
        // elaborated. Analyze only the omitted physical procedures, using the
        // same Slang DFA and resolved symbols, not remapped first-instance IDs.
        analysis::DefaultDFA dfa(context, symbol, true);
        dfa.run();
        if (dfa.bad)
            throw std::runtime_error("cannot analyze source procedure: " + symbol.getHierarchicalPath());
        analysis::AnalyzedProcedure result(context, symbol, nullptr, dfa);
        auto& sensitivity = result.getSensitivityList();
        procedures.push_back({&symbol, sensitivity.kind,
                              {sensitivity.reads.begin(), sensitivity.reads.end()},
                              {result.getCallExpressions().begin(), result.getCallExpressions().end()}});
    }

    void handle(const ast::ProceduralBlockSymbol& symbol) { add(symbol); }
    void handle(const ast::ContinuousAssignSymbol& symbol) { add(symbol); }

    void handle(const ast::NetSymbol& net) {
        auto initializer = net.getInitializer();
        if (!initializer)
            return;
        ast::ContinuousAssignSymbol expression(net.location, *initializer);
        expression.setParent(*net.getParentScope());
        analysis::DefaultDFA dfa(context, expression, false);
        dfa.run();
        if (dfa.bad)
            throw std::runtime_error("cannot analyze net driver: " + net.getHierarchicalPath());
        analysis::AnalyzedProcedure analyzed(context, expression, nullptr, dfa);
        auto& sensitivity = analyzed.getSensitivityList();
        procedures.push_back({&net, sensitivity.kind,
                              {sensitivity.reads.begin(), sensitivity.reads.end()}});
    }
};

// ASTSerializer does not serialize the signedness of predefined integer types.
// Export semantic facts keyed by that same compilation's type identity. These
// addresses join documents within one parse, never define public goal IDs.
struct IntegralFacts : behave::SourceDesignVisitor<IntegralFacts> {
    std::set<const ast::Type*> visited;
    std::map<uintptr_t, const ast::Type*> types;
    std::map<uintptr_t, const ast::ParameterSymbol*> arrayParameters;

    void add(const ast::Type& type) {
        if (!visited.insert(&type).second)
            return;
        if (type.isIntegral())
            types.emplace(reinterpret_cast<uintptr_t>(&type), &type);
        if (auto element = type.getArrayElementType())
            add(*element);
        if (&type.getCanonicalType() != &type)
            add(type.getCanonicalType());
    }

    template<typename T> void handle(const T& node) {
        if constexpr (std::is_same_v<T, ast::ParameterSymbol>) {
            if (node.getType().getCanonicalType().kind == ast::SymbolKind::FixedSizeUnpackedArrayType)
                arrayParameters.emplace(reinterpret_cast<uintptr_t>(&node), &node);
        }
        if constexpr (std::is_base_of_v<ast::Expression, T>)
            add(*node.type);
        else if constexpr (std::is_base_of_v<ast::ValueSymbol, T>)
            add(node.getType());
        else if constexpr (std::is_base_of_v<ast::Type, T>)
            add(node);
        visitDefault(node);
    }
};

// Export elaborated aggregate values, not their abbreviated display strings or
// initializer expressions. Reading a parameter does not execute its helper again.
static void writeConstant(JsonWriter& json, const ConstantValue& value) {
    if (value.isUnpacked()) {
        json.startArray();
        for (auto& element : value.elements())
            writeConstant(json, element);
        json.endArray();
    }
    else {
        json.writeValue(value.toString(SVInt::MAX_BITS, true));
    }
}

// Resolve only loop-header constants, not every expression in a potentially
// large datapath. Slang keeps the typed expression and its symbol identities;
// constant folding adds facts without pruning a body or a declared outcome.
struct LoopHeaders : behave::SourceDesignVisitor<LoopHeaders> {
    std::vector<const ast::ForLoopStatement*> loops;

    void handle(const ast::ForLoopStatement& loop) {
        loops.push_back(&loop);
        visitDefault(loop);
    }
};

// Resolve the emitted spelling in the process's scope, using the compiler's
// symbol identity. A same-named local is not evidence of the right dependency.
static std::string referenceTo(const ast::ValueSymbol& value, const ast::Scope& scope) {
    for (auto& candidate : behave::referenceCandidates(value)) {
        if (behave::lookupObserverReference(scope, candidate) == &value)
            return candidate;
    }
    throw std::runtime_error("cannot render sensitivity reference: " + value.getHierarchicalPath());
}

// Elaboration constants distinguish physical instances of one source process.
// Emit only references that the compiler resolves back to this exact parameter
// from the consumer's scope. Genvar iterations appear as implicit parameters.
// These facts select a dependency set; they never widen or infer that set.
using InstanceParameters = std::map<std::string, SVInt>;
using InstanceParameterCache = std::map<const ast::Scope*, InstanceParameters>;

static const InstanceParameters& instanceParameters(const ast::Scope& scope,
                                                    InstanceParameterCache& cache) {
    if (auto found = cache.find(&scope); found != cache.end())
        return found->second;
    std::map<std::string, SVInt> parameters;
    for (auto parent = &scope; parent; parent = parent->asSymbol().getParentScope()) {
        for (auto& member : parent->members()) {
            if (member.kind != ast::SymbolKind::Parameter)
                continue;
            auto& parameter = member.as<ast::ParameterSymbol>();
            auto& value = parameter.getValue();
            if (!value.isInteger() || value.integer().hasUnknown())
                continue;
            for (auto& candidate : behave::referenceCandidates(parameter)) {
                if (behave::lookupObserverReference(scope, candidate) == &parameter) {
                    parameters.emplace(candidate, value.integer());
                    break;
                }
            }
        }
    }
    return cache.emplace(&scope, std::move(parameters)).first->second;
}

static void writeInstanceParameters(JsonWriter& json, const InstanceParameters& parameters) {
    json.writeProperty("instance_parameters");
    json.startArray();
    for (auto& [reference, value] : parameters) {
        auto bits = value;
        bits.setSigned(false);
        json.startObject();
        json.writeProperty("reference");
        json.writeValue(reference);
        json.writeProperty("width");
        json.writeValue(uint64_t(value.getBitWidth()));
        json.writeProperty("bits");
        json.writeValue(bits.toString(LiteralBase::Hex, false, SVInt::MAX_BITS));
        json.endObject();
    }
    json.endArray();
}

static std::string packedSlice(const std::string& name, uint64_t low, uint64_t width) {
    return "(" + std::to_string(width) + "'($unsigned(" + name + ") >> " + std::to_string(low) + "))";
}

struct ReadProjection {
    std::string expression;
    uint64_t unpackedDimensions;
};

static uint64_t unpackedDimensions(const ast::Type& type) {
    auto element = &type;
    uint64_t dimensions = 0;
    while (element->getCanonicalType().kind == ast::SymbolKind::FixedSizeUnpackedArrayType) {
        ++dimensions;
        element = element->getArrayElementType();
    }
    return dimensions;
}

static void renderRange(const std::string& name, const ast::Type& type, uint64_t low,
                        uint64_t high, std::vector<ReadProjection>& terms) {
    auto width = type.getSelectableWidth();
    if (!width || low > high || high >= width)
        throw std::runtime_error("invalid compiler sensitivity range for " + name);
    if (low == 0 && high == width - 1) {
        // Whole arrays stay whole. Do not expand a dynamic read's complete
        // memory dependency into one generated term per memory cell.
        terms.push_back({name, unpackedDimensions(type)});
        return;
    }
    if (type.isIntegral()) {
        // Compiler ranges are physical bit offsets, independent of declared
        // packed direction or lower bound. A size cast retains only this range.
        terms.push_back({packedSlice(name, low, high - low + 1), 0});
        return;
    }
    if (type.getCanonicalType().kind != ast::SymbolKind::FixedSizeUnpackedArrayType)
        throw std::runtime_error("partial sensitivity of unsupported aggregate: " + name);
    auto& element = *type.getArrayElementType();
    auto elementWidth = element.getSelectableWidth();
    auto bounds = type.getFixedRange();
    auto index = [&](uint64_t ordinal) {
        return int64_t(bounds.left) + (bounds.isDescending() ? -int64_t(ordinal) : int64_t(ordinal));
    };
    auto first = low / elementWidth;
    auto last = high / elementWidth;
    auto select = [&](uint64_t ordinal) { return name + "[" + std::to_string(index(ordinal)) + "]"; };
    if (first == last) {
        renderRange(select(first), element, low % elementWidth, high % elementWidth, terms);
        return;
    }
    if (low % elementWidth) {
        renderRange(select(first++), element, low % elementWidth, elementWidth - 1, terms);
    }
    if (high % elementWidth != elementWidth - 1) {
        renderRange(select(last--), element, 0, high % elementWidth, terms);
    }
    // For a partial unpacked range, use its selected elements. Verilator's
    // event lowering does not support unpacked slice expressions (even behind
    // a packed cast). This is exact and does not include the other cells.
    // Cost is linear in the selected range; whole arrays take the branch above.
    for (auto ordinal = first; ordinal <= last; ++ordinal)
        renderRange(select(ordinal), element, 0, elementWidth - 1, terms);
}

static void writeCST(driver::Driver& driver, const std::string& filename) {
    std::ofstream output(filename);
    output.exceptions(std::ios::failbit | std::ios::badbit);
    JsonWriter json;
    json.startObject();
    json.writeProperty("syntaxTrees");
    json.startArray();
    syntax::CSTSerializer serializer(json, syntax::CSTJsonMode::Full);
    for (auto& tree : driver.syntaxTrees) {
        serializer.serialize(*tree);
        json.flushTo(output);
    }
    json.endArray();
    json.endObject();
    json.flushTo(output);
}

static void writeAST(ast::Compilation& compilation, const std::string& filename) {
    std::ofstream output(filename);
    output.exceptions(std::ios::failbit | std::ios::badbit);
    JsonWriter json;
    ast::ASTSerializer serializer(compilation, json);
    serializer.setIncludeSourceInfo(true);
    serializer.setDetailedTypeInfo(true);
    serializer.setTryConstantFold(false);
    serializer.startObject();
    serializer.writeProperty("design");
    serializer.serialize(compilation.getRoot());
    json.flushTo(output);
    serializer.writeProperty("definitions");
    serializer.startArray();
    for (auto definition : compilation.getDefinitions()) {
        serializer.serialize(*definition);
        json.flushTo(output);
    }
    serializer.endArray();
    IntegralFacts facts;
    compilation.getRoot().visit(facts);
    serializer.writeProperty("integral_types");
    serializer.startArray();
    for (auto [identity, type] : facts.types) {
        serializer.startObject();
        serializer.write("type_addr", uint64_t(identity));
        serializer.write("canonical_type_addr", uint64_t(reinterpret_cast<uintptr_t>(&type->getCanonicalType())));
        serializer.write("width", uint64_t(type->getBitWidth()));
        serializer.write("signed", type->isSigned());
        serializer.write("four_state", type->isFourState());
        serializer.endObject();
    }
    serializer.endArray();
    LoopHeaders headers;
    serializer.startArray("parameter_values");
    for (auto [identity, parameter] : facts.arrayParameters) {
        serializer.startObject();
        serializer.write("symbol_addr", uint64_t(identity));
        serializer.writeProperty("value");
        writeConstant(json, parameter->getValue());
        serializer.endObject();
    }
    serializer.endArray();
    compilation.getRoot().visit(headers);
    serializer.setTryConstantFold(true);
    serializer.startArray("loop_headers");
    for (auto loop : headers.loops) {
        serializer.startObject();
        serializer.startArray("initializers");
        for (auto init : loop->initializers)
            serializer.serialize(*init);
        serializer.endArray();
        serializer.startArray("declaredInitializers");
        for (auto var : loop->loopVars) {
            if (auto init = var->getInitializer())
                serializer.serialize(*init);
        }
        serializer.endArray();
        if (loop->stopExpr)
            serializer.write("stopExpr", *loop->stopExpr);
        serializer.startArray("steps");
        for (auto step : loop->steps)
            serializer.serialize(*step);
        serializer.endArray();
        serializer.endObject();
    }
    serializer.endArray();
    serializer.setTryConstantFold(false);
    serializer.endObject();
    json.flushTo(output);
}

static int run(int argc, char** argv) {
    driver::Driver driver;
    driver.addStandardArgs();
    std::optional<std::string> cstFile, astFile, sensitivityFile, sourceCheckFile;
    std::optional<bool> version;
    driver.cmdLine.add("--version", version, "Print the locked frontend revision");
    driver.cmdLine.add("--sensitivity-json", sensitivityFile, "Write process dependencies", "<file>");
    driver.cmdLine.add("--cst-json", cstFile, "Write the original concrete syntax", "<file>");
    driver.cmdLine.add("--ast-json", astFile, "Write the selected typed design", "<file>");
    driver.cmdLine.add("--source-check-json", sourceCheckFile,
                      "Check source and retain typed language diagnostics", "<file>");
    if (!driver.parseCommandLine(argc, argv))
        return 1;
    if (version.value_or(false)) {
        std::cout << "behave-rtl-frontend/23 slang-source=8acc660a2\n";
        return 0;
    }
    if (!driver.processOptions())
        return 1;
    auto diagnostics = sourceCheckFile ?
        std::make_shared<behave::SourceDiagnostics>(*sourceCheckFile) : nullptr;
    if (diagnostics)
        driver.diagEngine.addClient(diagnostics);
    if (!driver.parseAllSources())
        return 1;
    // Observations own physical instance scopes. Canonical body sharing would
    // report only the first instance's dependencies and write subscribers.
    // Elaborate each instance, then let the source planner share emitted text
    // only when the compiler-bound relative references really agree.
    driver.options.compilationFlags[ast::CompilationFlags::DisableInstanceCaching] = true;
    auto compilation = driver.createCompilation();
    driver.reportCompilation(*compilation, true);
    if (!driver.reportDiagnostics(true)) {
        if (diagnostics)
            diagnostics->write();
        return diagnostics ? 5 : 2;
    }

    // Follow the installed driver's analysis lifecycle. The listener only
    // retains results; Slang owns symbol resolution and all read/write rules.
    compilation->getAllDiagnostics();
    compilation->freeze();
    auto analysisOptions = driver.getAnalysisOptions();
    // The locked Verilator execution observes module reads inside continuous
    // assignment functions. Keep whole-symbol sensitivity for partial selects;
    // this is a backend convention, not the always_comb LSP rule.
    analysisOptions.flags |= analysis::AnalysisFlags::InlineContAssignFunctionReads;
    // The pinned execution backend inlines non-suspending task bodies in
    // always_comb / always_latch before dependency scheduling. Reuse Slang's
    // bound call/read/driver analysis for this explicit backend convention;
    // native helper admission still excludes suspension and recursion.
    analysisOptions.flags |= analysis::AnalysisFlags::InlineCombTaskCalls;
    analysis::AnalysisManager manager(analysisOptions);
    if (diagnostics) {
        // Source attribution requires language analysis only. No native event
        // scheduling, dependency projection or candidate instrumentation runs.
        manager.analyze(*compilation);
        driver.diagEngine.issue(manager.getDiagnostics());
        bool accepted = driver.reportDiagnostics(true);
        diagnostics->write();
        return accepted ? 0 : 5;
    }
    std::mutex mutex;
    std::vector<ProcessReads> procedures;
    manager.addListener([&](const analysis::AnalyzedProcedure& procedure) {
        if (procedure.analyzedSymbol->kind == ast::SymbolKind::ProceduralBlock ||
            procedure.analyzedSymbol->kind == ast::SymbolKind::ContinuousAssign) {
            auto& sensitivity = procedure.getSensitivityList();
            std::lock_guard lock(mutex);
            procedures.push_back({procedure.analyzedSymbol,
                                  sensitivity.kind,
                                  {sensitivity.reads.begin(), sensitivity.reads.end()},
                                  {procedure.getCallExpressions().begin(), procedure.getCallExpressions().end()}});
        }
    });
    manager.analyze(*compilation);
    SourceProcedureReads sourceProcedures(manager, procedures);
    compilation->getRoot().visit(sourceProcedures);
    driver.diagEngine.issue(manager.getDiagnostics());
    if (!driver.reportDiagnostics(true))
        return 3;
    compilation->unfreeze(); // Rendering invokes compiler name lookup.
    if (cstFile)
        writeCST(driver, *cstFile);
    if (astFile)
        writeAST(*compilation, *astFile);

    behave::SourceWrites writes;
    compilation->getRoot().visit(writes);
    writes.closeReferences();

    auto& sources = driver.sourceManager;
    for (auto& procedure : procedures) {
        // Elaboration constants cannot schedule a new activation. Keep runtime
        // variables even when an expression folds to a constant; only compiler
        // symbol kinds that are immutable by the language are excluded here.
        std::erase_if(procedure.reads, [](const auto& read) {
            return read.symbol->kind == ast::SymbolKind::Parameter ||
                   read.symbol->kind == ast::SymbolKind::EnumValue ||
                   read.symbol->kind == ast::SymbolKind::Specparam;
        });
    }
    std::sort(procedures.begin(), procedures.end(), [](auto& a, auto& b) {
        auto left = a.symbol->getHierarchicalPath();
        auto right = b.symbol->getHierarchicalPath();
        if (left != right)
            return left < right;
        return a.symbol->location.offset() < b.symbol->location.offset();
    });
    JsonWriter json;
    json.setPrettyPrint(true);
    json.startArray();
    // Compiler lookup is shared by every occurrence in a lexical writer
    // family. A large generated array must not resolve the same path again for
    // every physical write/read pair.
    using ReferenceKey = std::tuple<const ast::Scope*, const ast::ValueSymbol*,
                                    SourceLocation, SourceLocation>;
    std::map<ReferenceKey, std::string> references;
    // Bound physical scope identity is valid only within this compilation.
    // Cache empty scopes too: flat netlists have many processes but no parameters.
    InstanceParameterCache parameters;
    behave::TaskFrameInputs taskFrames(manager);
    for (auto& procedure : procedures) {
        auto& symbol = *procedure.symbol;
        auto range = symbol.getSyntax()->sourceRange();
        json.startObject();
        json.writeProperty("scope");
        json.writeValue(symbol.getParentScope()->asSymbol().getHierarchicalPath());
        json.writeProperty("lexical_scope");
        json.writeValue(symbol.getParentScope()->asSymbol().getLexicalPath());
        json.writeProperty("scope_id");
        json.writeValue(uint64_t(reinterpret_cast<uintptr_t>(&symbol.getParentScope()->asSymbol())));
        json.writeProperty("procedure");
        json.writeValue(symbol.kind == ast::SymbolKind::Net ? "NetInitializer" :
                        symbol.kind == ast::SymbolKind::ContinuousAssign ? "ContinuousAssign" :
                        toString(symbol.as<ast::ProceduralBlockSymbol>().procedureKind));
        json.writeProperty("file");
        json.writeValue(sources.getFileName(range.start()));
        json.writeProperty("start");
        json.writeValue(uint64_t(range.start().offset()));
        json.writeProperty("end");
        json.writeValue(uint64_t(range.end().offset()));
        json.writeProperty("line");
        json.writeValue(uint64_t(sources.getLineNumber(range.start())));
        json.writeProperty("sensitivity_kind");
        json.writeValue(int64_t(procedure.kind));
        const auto* block = symbol.as_if<ast::ProceduralBlockSymbol>();
        json.writeProperty("stationary_value_events");
        json.writeValue(block && behave::stationaryValueEvents(*block));
        json.writeProperty("inlined_task_dependencies");
        const bool implicitTasks = block &&
            (block->procedureKind == ast::ProceduralBlockKind::AlwaysComb ||
             block->procedureKind == ast::ProceduralBlockKind::AlwaysLatch);
        std::set<std::string> frameInputs;
        if (implicitTasks) {
            for (auto call : procedure.calls) {
                if (!taskFrames.hasTask(*call))
                    continue; // No second DFA over ordinary function-only logic.
                frameInputs = taskFrames.forProcess(*block);
                break;
            }
        }
        json.writeValue(implicitTasks && frameInputs.empty());
        json.writeProperty("task_frame_inputs");
        json.startArray();
        for (auto& input : frameInputs)
            json.writeValue(input);
        json.endArray();
        writeInstanceParameters(json, instanceParameters(*symbol.getParentScope(), parameters));
        std::vector<std::string> triggers;
        for (auto& read : procedure.reads) {
            auto name = referenceTo(*read.symbol, *symbol.getParentScope());
            std::vector<ReadProjection> projections;
            renderRange(name, read.symbol->getType(), read.bitRange.first, read.bitRange.second, projections);
            for (auto& projection : projections)
                triggers.push_back(projection.expression);
        }
        std::sort(triggers.begin(), triggers.end());
        triggers.erase(std::unique(triggers.begin(), triggers.end()), triggers.end());
        json.writeProperty("triggers");
        json.startArray();
        for (auto& term : triggers)
            json.writeValue(term);
        json.endArray();
        json.writeProperty("reads");
        json.startArray();
        for (auto& read : procedure.reads) {
            json.startObject();
            // Like type identities in AST facts, these addresses join this
            // compilation only. Public occurrences retain source coordinates.
            json.writeProperty("symbol_id");
            json.writeValue(uint64_t(reinterpret_cast<uintptr_t>(&*read.symbol)));
            json.writeProperty("scope_id");
            json.writeValue(uint64_t(reinterpret_cast<uintptr_t>(&read.symbol->getParentScope()->asSymbol())));
            json.writeProperty("symbol");
            json.writeValue(read.symbol->getHierarchicalPath());
            json.writeProperty("lexical_symbol");
            json.writeValue(read.symbol->getLexicalPath());
            json.writeProperty("type");
            json.writeValue(read.symbol->getType().toString());
            json.writeProperty("width");
            json.writeValue(uint64_t(read.symbol->getType().getSelectableWidth()));
            json.writeProperty("low");
            json.writeValue(read.bitRange.first);
            json.writeProperty("high");
            json.writeValue(read.bitRange.second);
            // Reuse the same typed range renderer at an original write site.
            // Its bound lvalue supplies the base; no variable is found by name.
            std::vector<ReadProjection> projections;
            renderRange("__behave_source_write_base", read.symbol->getType(),
                        read.bitRange.first, read.bitRange.second, projections);
            json.writeProperty("projections");
            json.startArray();
            for (auto& projection : projections) {
                json.startObject();
                json.writeProperty("expression");
                json.writeValue(projection.expression);
                json.writeProperty("unpacked_dimensions");
                json.writeValue(projection.unpackedDimensions);
                json.endObject();
            }
            json.endArray();
            json.writeProperty("write_references");
            json.startArray();
            for (auto& write : writes.bySymbol[&*read.symbol]) {
                auto range = write.value->sourceRange;
                json.startObject();
                json.writeProperty("file");
                json.writeValue(sources.getFileName(range.start()));
                json.writeProperty("start");
                json.writeValue(uint64_t(range.start().offset()));
                json.writeProperty("end");
                json.writeValue(uint64_t(range.end().offset()));
                json.writeProperty("symbol_id");
                json.writeValue(uint64_t(reinterpret_cast<uintptr_t>(&write.value->symbol)));
                if (&write.value->symbol != &*read.symbol) {
                    // A ref write retains its formal's source coordinates, but
                    // snapshots the actual subscribed storage. The bound index
                    // is already held by the language runtime, never re-read.
                    auto& family = writes.families.at(&*read.symbol).at({range.start(), range.end()});
                    auto reference = referenceTo(*read.symbol, *write.scope);
                    for (auto scope : family) {
                        if (behave::lookupObserverReference(*scope, reference) != &*read.symbol)
                            throw std::runtime_error("ref subscriber has no shared storage reference");
                    }
                    json.writeProperty("storage_reference");
                    json.writeValue(reference);
                }
                json.writeProperty("scope_reference");
                auto key = ReferenceKey(symbol.getParentScope(), &*read.symbol,
                                        range.start(), range.end());
                auto found = references.find(key);
                if (found == references.end()) {
                    auto& family = writes.families.at(&*read.symbol).at({range.start(), range.end()});
                    found = references.emplace(key, behave::scopeReference(
                        *symbol.getParentScope(), family)).first;
                }
                json.writeValue(found->second);
                // A running process is not waiting for its own blocking write.
                // Use bound physical procedure identity, not a shared source
                // span: another instance's same lexical process can be armed.
                json.writeProperty("self_blocking");
                json.writeValue(write.blocking && write.procedure == &symbol);
                json.endObject();
            }
            json.endArray();
            json.endObject();
        }
        json.endArray();
        json.endObject();
    }
    json.endArray();
    json.writeNewLine();
    if (sensitivityFile) {
        std::ofstream output(*sensitivityFile);
        output.exceptions(std::ios::failbit | std::ios::badbit);
        json.flushTo(output);
    }
    else {
        json.flushTo(std::cout);
    }
    return 0;
}

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    }
    catch (const std::exception& error) {
        std::cerr << "behave-rtl-frontend: " << error.what() << '\n';
        return 1;
    }
}
