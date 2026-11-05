// Qualify implicit task frames without changing their original storage.
// Slang owns branch joins, loop analysis, symbol binding and assigned ranges.
#pragma once

#include <algorithm>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "slang/analysis/DataFlowAnalysis.h"
#include "slang/ast/ASTVisitor.h"

namespace behave {

using TaskRanges = std::map<const slang::ast::ValueSymbol*,
                            std::vector<std::pair<uint64_t, uint64_t>>>;

struct TaskFlowSummary {
    TaskRanges inputs, writes, definiteWrites;
    std::set<std::string> issues;
};

// Summaries compose at the call site, so a caller's initialization is visible
// to its helpers. The compiler still owns control flow, joins and bit ranges.
class TaskEntryReads : public slang::analysis::DataFlowAnalysis<
                           TaskEntryReads, slang::analysis::DataFlowState> {
    using Base = slang::analysis::DataFlowAnalysis<TaskEntryReads,
                                                 slang::analysis::DataFlowState>;
    using ValueSymbol = slang::ast::ValueSymbol;
    using Bounds = std::pair<uint64_t, uint64_t>;
    using ReadSet = slang::analysis::DFAResults::ReadSet;
    ReadSet entryReads, calledWrites;
    std::function<const TaskFlowSummary&(const slang::ast::SubroutineSymbol&)> summarize;
    std::set<std::string> issues;

    static bool localTo(const ValueSymbol& symbol, const slang::ast::Symbol& owner) {
        auto scope = symbol.getParentScope();
        while (scope && scope->asSymbol().kind == slang::ast::SymbolKind::StatementBlock)
            scope = scope->asSymbol().getParentScope();
        return scope && &scope->asSymbol() == &owner;
    }

    void read(const ValueSymbol& symbol, Bounds bounds) {
        using namespace slang::ast;
        if (!this->getState().reachable)
            return;
        if (auto variable = symbol.as_if<VariableSymbol>();
            variable && variable->lifetime == VariableLifetime::Automatic)
            return; // Includes compiler-created for-header scopes.
        if (localTo(symbol, this->rootSymbol)) {
            if (auto formal = symbol.as_if<FormalArgumentSymbol>();
                formal && formal->direction != ArgumentDirection::Out)
                return; // Input / inout values are copied in at every call.
        }
        // Preserve only the not-yet-written portion. A prior write to x[0]
        // followed by reading x must not invent an entry read of x[0].
        const auto& state = this->getState().assigned;
        auto slot = symbolToSlot.find(&symbol);
        auto next = bounds.first;
        if (slot != symbolToSlot.end() && slot->second < state.size()) {
            const auto& ranges = state[slot->second];
            for (auto it = ranges.find(bounds); it.valid(); ++it) {
                const auto [first, last] = it.bounds();
                if (first > next)
                    entryReads[&symbol].unionWith({next, first - 1}, {}, bitMapAllocator);
                if (last >= bounds.second)
                    return;
                next = last + 1;
            }
        }
        entryReads[&symbol].unionWith({next, bounds.second}, {}, bitMapAllocator);
    }

    void assign(const ValueSymbol& symbol, Bounds bounds) {
        auto [slot, inserted] = symbolToSlot.try_emplace(&symbol, uint32_t(lvalues.size()));
        if (inserted)
            lvalues.emplace_back(symbol);
        auto& state = this->getState().assigned;
        if (slot->second >= state.size())
            state.resize(slot->second + 1);
        state[slot->second].unionWith(bounds, {}, bitMapAllocator);
    }

    static void saveRanges(TaskRanges& output, const ReadSet& input) {
        for (const auto& [symbol, ranges] : input)
            for (auto it = ranges.begin(); it != ranges.end(); ++it)
                output[symbol].push_back(it.bounds());
    }

public:
    using Base::handle;

    TaskEntryReads(slang::analysis::AnalysisContext& context,
                   const slang::ast::Symbol& symbol, decltype(summarize) summarize) :
        Base(context, symbol, false), summarize(std::move(summarize)) {
        // This analysis describes one non-suspending activation, not arbitrary
        // concurrent procedures. A static counter initialized by its for header
        // has the same iteration values here as an automatic counter.
        this->options.flags |= slang::analysis::AnalysisFlags::UnrollStaticLoopInitializers;
    }

    void handle(const slang::ast::AssignmentExpression& expr) {
        if (expr.isLValueArg()) {
            Base::handle(expr);
            return;
        }
        if (expr.isNonBlocking())
            issues.insert("implicit task frame has deferred writes");
        // A write becomes available after its RHS, not before. The default
        // driver/latch DFA can record a whole expression's writes early; entry
        // reads additionally need this ordering for n=n+1 and n+=1.
        if (expr.isCompound())
            this->visit(expr.left());
        this->template visitSequenced<true>(expr.right(), expr.left());
    }

    void handle(const slang::ast::UnaryExpression& expr) {
        if (slang::ast::OpInfo::isLValue(expr.op))
            this->visit(expr.operand());
        Base::handle(expr);
    }

    void handle(const slang::ast::VariableDeclStatement& statement) {
        this->visitStmt(statement);
        const auto& symbol = statement.symbol;
        if (symbol.lifetime == slang::ast::VariableLifetime::Automatic && symbol.getInitializer())
            assign(symbol, {0, symbol.getType().getSelectableWidth() - 1});
    }

    void noteRead(const slang::ast::ValuePath& path) {
        read(*path.rootSymbol(), path.lspBounds);
    }

    bool recordsDefiniteWrite(const slang::ast::ValuePath& path) const {
        // The LSP of a[i] is the whole array when i is unknown. It is a possible
        // write, not proof that every element has been initialized.
        return path.isFullyStatic();
    }

    void handle(const slang::ast::CallExpression& expr) {
        using namespace slang::ast;
        if (!this->getState().reachable)
            return;
        if (expr.isSystemCall() || expr.thisClass()) {
            Base::handle(expr);
            return;
        }
        auto& callee = *std::get<const SubroutineSymbol*>(expr.subroutine);
        const auto formals = callee.getArguments();
        const auto args = expr.arguments();
        expr.visitExprsNoArgs(*this);
        // Read *all* incoming values before copying back any outputs. In
        // particular f(output x, input x) must read the caller's old x.
        for (size_t i = 0; i < args.size(); ++i) {
            const auto direction = formals[i]->direction;
            auto arg = args[i];
            if (auto assignment = arg->as_if<AssignmentExpression>();
                assignment && assignment->isLValueArg())
                arg = &assignment->left();
            if (direction == ArgumentDirection::Out) {
                // Output selectors are evaluated on entry, but not the value
                // being overwritten. The normal DFA visits them during copyback.
                ValuePath::visitPaths(*arg, this->getEvalContext(), [&](const ValuePath& path) {
                    const auto* value = path.rootSymbol();
                    if (!value)
                        return;
                    for (const auto& selector : path) {
                        if (auto select = selector.as_if<ElementSelectExpression>())
                            this->visit(select->selector());
                        else if (auto select = selector.as_if<RangeSelectExpression>()) {
                            this->visit(select->left());
                            this->visit(select->right());
                        }
                    }
                }, true);
            }
            else
                this->visit(*arg);
            if (direction == ArgumentDirection::Ref)
                issues.insert("implicit task frame has reference aliases");
        }
        const auto& summary = summarize(callee);
        issues.insert(summary.issues.begin(), summary.issues.end());
        for (const auto& [symbol, ranges] : summary.inputs)
            for (auto bounds : ranges)
                read(*symbol, bounds);
        for (const auto& [symbol, ranges] : summary.writes)
            for (auto bounds : ranges)
                calledWrites[symbol].unionWith(bounds, {}, bitMapAllocator);
        for (const auto& [symbol, ranges] : summary.definiteWrites)
            for (auto bounds : ranges)
                assign(*symbol, bounds);
        for (size_t i = 0; i < args.size(); ++i) {
            if (formals[i]->direction == ArgumentDirection::In ||
                formals[i]->direction == ArgumentDirection::Ref)
                continue;
            // The compiler's pseudo-assignment identifies the exact copy-out
            // lvalue, including arrays and concatenations.
            this->visitLValue(*args[i]);
        }
    }

    TaskFlowSummary result() {
        using namespace slang::ast;
        this->run();
        for (const auto& returned : this->getReturnStates())
            this->joinState(this->getState(), returned);
        if (this->bad || !this->getTimedStatements().empty())
            issues.insert("unavailable non-suspending helper dataflow");
        if (auto subroutine = this->rootSymbol.as_if<SubroutineSymbol>()) {
            for (auto formal : subroutine->getArguments()) {
                if (formal->direction == ArgumentDirection::Out)
                    read(*formal, {0, formal->getType().getSelectableWidth() - 1});
            }
        }
        TaskFlowSummary output;
        output.issues = std::move(issues);
        saveRanges(output.inputs, entryReads);
        saveRanges(output.writes, calledWrites);
        for (const auto& value : this->getLValues()) {
            const auto* symbol = value.symbol.get();
            for (auto it = value.assigned.begin(); it != value.assigned.end(); ++it)
                output.writes[symbol].push_back(it.bounds());
            const auto slot = symbolToSlot.find(symbol)->second;
            const auto& state = this->getState().assigned;
            if (slot < state.size())
                for (auto it = state[slot].begin(); it != state[slot].end(); ++it)
                    output.definiteWrites[symbol].push_back(it.bounds());
        }
        // Callee-local writes cannot initialize the caller's namespace. Reads
        // of retained locals, however, remain explicit capability issues.
        for (auto it = output.inputs.begin(); it != output.inputs.end();) {
            if (localTo(*it->first, this->rootSymbol)) {
                output.issues.insert(it->first->getHierarchicalPath());
                it = output.inputs.erase(it);
            }
            else
                ++it;
        }
        std::erase_if(output.writes, [&](const auto& value) { return localTo(*value.first, this->rootSymbol); });
        std::erase_if(output.definiteWrites, [&](const auto& value) { return localTo(*value.first, this->rootSymbol); });
        return output;
    }
};

class TaskFrameInputs {
    slang::analysis::AnalysisContext context;
    std::map<const slang::ast::SubroutineSymbol*, TaskFlowSummary> cache;
    std::set<const slang::ast::SubroutineSymbol*> active;
    std::map<const slang::ast::SubroutineSymbol*, bool> taskClosures;
    const TaskFlowSummary recursive{{}, {}, {}, {"recursive helper frame"}};

    const TaskFlowSummary& forSubroutine(const slang::ast::SubroutineSymbol& callee) {
        if (auto prior = cache.find(&callee); prior != cache.end())
            return prior->second;
        if (!active.insert(&callee).second)
            return recursive;
        TaskEntryReads flow(context, callee, [&](const auto& nested) -> const auto& {
            return forSubroutine(nested);
        });
        auto summary = flow.result();
        active.erase(&callee);
        return cache.emplace(&callee, std::move(summary)).first->second;
    }

    struct TaskCalls : slang::ast::ASTVisitor<TaskCalls, slang::ast::VisitFlags::AllGood> {
        std::function<bool(const slang::ast::CallExpression&)> inspect;
        bool found = false;
        explicit TaskCalls(decltype(inspect) inspect) : inspect(std::move(inspect)) {}
        void handle(const slang::ast::CallExpression& call) {
            found |= inspect(call);
            visitDefault(call);
        }
    };

public:
    explicit TaskFrameInputs(slang::analysis::AnalysisManager& manager) : context(manager) {}

    bool hasTask(const slang::ast::CallExpression& call) {
        using namespace slang::ast;
        if (call.isSystemCall() || call.thisClass())
            return false;
        if (call.getSubroutineKind() == SubroutineKind::Task)
            return true;
        const auto* callee = std::get<const SubroutineSymbol*>(call.subroutine);
        auto [entry, inserted] = taskClosures.try_emplace(callee, false);
        if (inserted) {
            TaskCalls calls([&](const auto& nested) { return hasTask(nested); });
            callee->getBody().visit(calls);
            entry->second = calls.found;
        }
        return entry->second;
    }

    std::set<std::string> forProcess(const slang::ast::ProceduralBlockSymbol& process) {
        TaskEntryReads flow(context, process, [&](const auto& callee) -> const auto& {
            return forSubroutine(callee);
        });
        auto summary = flow.result();
        for (const auto& [symbol, reads] : summary.inputs) {
            auto writes = summary.writes.find(symbol);
            if (writes == summary.writes.end())
                continue;
            for (auto [readLow, readHigh] : reads)
                for (auto [writeLow, writeHigh] : writes->second)
                    if (readLow <= writeHigh && writeLow <= readHigh)
                        summary.issues.insert(symbol->getHierarchicalPath());
        }
        return summary.issues;
    }
};

} // namespace behave
