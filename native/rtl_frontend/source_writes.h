// Bound procedural lvalues and scope references for source-event subscribers.
#pragma once

#include <algorithm>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>

#include "slang/ast/ASTVisitor.h"
#include "slang/ast/Lookup.h"
#include "slang/ast/expressions/AssignmentExpressions.h"
#include "slang/ast/expressions/CallExpression.h"
#include "slang/ast/expressions/MiscExpressions.h"
#include "slang/ast/expressions/OperatorExpressions.h"
#include "slang/ast/expressions/SelectExpressions.h"
#include "slang/ast/symbols/BlockSymbols.h"
#include "slang/ast/symbols/CompilationUnitSymbols.h"
#include "slang/ast/symbols/InstanceSymbols.h"
#include "slang/ast/symbols/ParameterSymbols.h"
#include "source_design.h"

namespace behave {

namespace ast = slang::ast;

struct SourceWrite {
    const ast::ValueExpressionBase* value;
    const ast::Scope* scope;
    const ast::ProceduralBlockSymbol* procedure;
    bool blocking;
};

// Retain the lexical scope at the actual lvalue occurrence. The declaration
// scope of the storage is not enough: a hierarchical write may originate in
// another instance, generate block, or helper. Select indices are read, not
// written; mutations within them are visited independently.
struct SourceWrites : SourceDesignVisitor<SourceWrites> {
    using SourceSpan = std::pair<slang::SourceLocation, slang::SourceLocation>;
    std::map<const ast::ValueSymbol*, std::vector<SourceWrite>> bySymbol;
    std::map<const ast::ValueSymbol*, std::map<SourceSpan, std::set<const ast::Scope*>>> families;
    std::set<std::pair<const ast::ValueExpressionBase*, const ast::Scope*>> seen;
    std::map<const ast::ValueSymbol*, std::set<const ast::ValueSymbol*>> aliases;
    const ast::Scope* scope = nullptr;
    const ast::ProceduralBlockSymbol* procedure = nullptr;

    static const ast::ValueSymbol* storage(const ast::Expression& expression) {
        switch (expression.kind) {
            case ast::ExpressionKind::NamedValue:
            case ast::ExpressionKind::HierarchicalValue:
                return &expression.as<ast::ValueExpressionBase>().symbol;
            case ast::ExpressionKind::ElementSelect:
                return storage(expression.as<ast::ElementSelectExpression>().value());
            case ast::ExpressionKind::RangeSelect:
                return storage(expression.as<ast::RangeSelectExpression>().value());
            case ast::ExpressionKind::MemberAccess:
                return storage(expression.as<ast::MemberAccessExpression>().value());
            default:
                return nullptr;
        }
    }

    void handle(const ast::CallExpression& expression) {
        if (!expression.isSystemCall() && !expression.thisClass()) {
            auto& callee = *std::get<const ast::SubroutineSymbol*>(expression.subroutine);
            auto formals = callee.getArguments();
            auto actuals = expression.arguments();
            for (size_t i = 0; i < formals.size(); ++i) {
                if (formals[i]->direction == ast::ArgumentDirection::Ref) {
                    if (auto actual = storage(*actuals[i]))
                        aliases[formals[i]].insert(actual);
                }
            }
        }
        visitDefault(expression);
    }

    // A ref formal writes the caller's storage immediately, unlike copy-out
    // ports. Follow bound symbols through nested calls, retaining the primitive
    // write's own span and scope. Read-range watches will inspect the actual
    // storage there, so dynamic selectors are neither re-evaluated nor guessed.
    void closeReferences() {
        if (aliases.empty())
            return;
        auto direct = bySymbol;
        for (auto& [formal, targets] : aliases) {
            std::vector<const ast::ValueSymbol*> pending(targets.begin(), targets.end());
            std::set<const ast::ValueSymbol*> visited{formal};
            while (!pending.empty()) {
                auto target = pending.back();
                pending.pop_back();
                if (!visited.insert(target).second)
                    continue;
                for (auto& write : direct[formal]) {
                    bySymbol[target].push_back(write);
                    auto range = write.value->sourceRange;
                    families[target][{range.start(), range.end()}].insert(write.scope);
                }
                if (auto nested = aliases.find(target); nested != aliases.end())
                    pending.insert(pending.end(), nested->second.begin(), nested->second.end());
            }
        }
    }

    void add(const ast::Expression& expression, bool blocking) {
        switch (expression.kind) {
            case ast::ExpressionKind::NamedValue:
            case ast::ExpressionKind::HierarchicalValue: {
                auto& value = expression.as<ast::ValueExpressionBase>();
                if (seen.emplace(&value, scope).second) {
                    bySymbol[&value.symbol].push_back({&value, scope, procedure, blocking});
                    auto range = value.sourceRange;
                    families[&value.symbol][{range.start(), range.end()}].insert(scope);
                }
                break;
            }
            case ast::ExpressionKind::ElementSelect:
                add(expression.as<ast::ElementSelectExpression>().value(), blocking);
                break;
            case ast::ExpressionKind::RangeSelect:
                add(expression.as<ast::RangeSelectExpression>().value(), blocking);
                break;
            case ast::ExpressionKind::MemberAccess:
                add(expression.as<ast::MemberAccessExpression>().value(), blocking);
                break;
            case ast::ExpressionKind::Concatenation:
                for (auto operand : expression.as<ast::ConcatenationExpression>().operands())
                    add(*operand, blocking);
                break;
            default:
                // Other lvalue kinds require their own lowering rule. They
                // cannot supply the variable-bound notifications consumed here.
                break;
        }
    }

    void handle(const ast::AssignmentExpression& expression) {
        add(expression.left(), !expression.isNonBlocking());
        visitDefault(expression);
    }

    void handle(const ast::UnaryExpression& expression) {
        switch (expression.op) {
            case ast::UnaryOperator::Preincrement:
            case ast::UnaryOperator::Predecrement:
            case ast::UnaryOperator::Postincrement:
            case ast::UnaryOperator::Postdecrement:
                add(expression.operand(), true);
                break;
            default:
                break;
        }
        visitDefault(expression);
    }

    void handle(const ast::BlockStatement& block) {
        auto previous = scope;
        if (block.blockSymbol)
            scope = block.blockSymbol;
        visitDefault(block);
        scope = previous;
    }

    void handle(const ast::ContinuousAssignSymbol&) {}

    void handle(const ast::ProceduralBlockSymbol& node) {
        auto previous = procedure;
        procedure = &node;
        visitDefault(node);
        procedure = previous;
    }

    template<typename T> void handle(const T& node) {
        auto previous = scope;
        if constexpr (std::is_base_of_v<ast::Scope, T>)
            scope = &node;
        visitDefault(node);
        scope = previous;
    }
};

inline const ast::Scope* resolvedScope(const ast::Symbol* symbol) {
    if (!symbol)
        return nullptr;
    if (auto instance = symbol->as_if<ast::InstanceSymbol>())
        return &instance->body;
    return symbol->as_if<ast::Scope>();
}

// Instrumentation can name compiler-generated scopes that user source cannot
// legally reference. Allow those names only while validating emitted observer
// references; the original compilation's language flags remain unchanged.
// Callers must still compare the result to the exact bound symbol or scope.
inline const ast::Symbol* lookupObserverReference(const ast::Scope& scope,
                                                 std::string_view name) {
    return scope.lookupName(name, ast::LookupLocation::max,
                            ast::LookupFlags::AllowUnnamedGenerate);
}

// Prefer scope-relative spellings over an absolute first-instance path. Every
// candidate is checked by compiler lookup before use; hierarchical boundaries
// come from compiler scopes, not splitting identifier text (which may be escaped).
inline std::vector<std::string> referenceCandidates(const ast::Symbol& symbol) {
    std::vector<std::string> result;
    if (!symbol.name.empty())
        result.push_back("\\" + std::string(symbol.name) + " ");
    auto path = symbol.getHierarchicalPath();
    for (auto parent = symbol.getHierarchicalParent(); parent;
         parent = parent->asSymbol().getHierarchicalParent()) {
        auto prefix = parent->asSymbol().getHierarchicalPath() + ".";
        if (path.starts_with(prefix))
            result.push_back(path.substr(prefix.size()));
    }
    result.push_back(symbol.getLexicalPath());
    result.push_back(path);
    return result;
}

inline bool denotesScope(const ast::Scope& target, const ast::Scope& from,
                         const std::string& reference) {
    if (resolvedScope(lookupObserverReference(from, reference)) == &target)
        return true;
    // An upward module-definition name can resolve as a member prefix rather
    // than a standalone scope. Check an original direct member in that case.
    for (auto& member : target.members()) {
        if (member.name.empty())
            continue;
        auto name = "\\" + std::string(member.name) + " ";
        auto expected = lookupObserverReference(target, name);
        if (expected && expected->getParentScope() == &target &&
            lookupObserverReference(from, reference + "." + name) == expected)
            return true;
    }
    return false;
}

// Preserve common generate-loop selectors as their compiler-bound localparams.
// Physical indices otherwise create a different source template per iteration.
// Scope identity, not a coincidentally equal integer, establishes the join; a
// procedure-local variable shadowing the genvar prevents this substitution.
inline std::string parametricScopeSuffix(const ast::Scope& target, const ast::Scope& root,
                                         const ast::Scope& writer, const ast::Scope& boundary) {
    auto rootPath = root.asSymbol().getHierarchicalPath();
    auto suffix = target.asSymbol().getHierarchicalPath().substr(rootPath.size());
    for (auto current = &target; current != &root;
         current = current->asSymbol().getHierarchicalParent()) {
        auto block = current->asSymbol().as_if<ast::GenerateBlockSymbol>();
        if (!block || !block->getArrayIndex())
            continue;
        for (auto& member : block->members()) {
            auto parameter = member.as_if<ast::ParameterSymbol>();
            if (!parameter || !parameter->isFromGenvar())
                continue;
            auto name = "\\" + std::string(parameter->name) + " ";
            if (lookupObserverReference(writer, name) != parameter ||
                lookupObserverReference(boundary, name) != parameter)
                continue;
            auto arrayPath = block->getParentScope()->asSymbol().getHierarchicalPath();
            auto blockPath = block->getHierarchicalPath();
            // Inner selectors are replaced first, leaving ancestor offsets intact.
            suffix.replace(arrayPath.size() - rootPath.size(), blockPath.size() - arrayPath.size(),
                           "[" + name + "]");
        }
    }
    return suffix;
}

// An inserted generate helper must not rebind a short sibling-scope spelling.
// Anchor nonlocal references at a module definition and validate lookup from
// both the write and its enclosing declaration scope, where helpers are added.
// Definition names are instance-relative: two Cell copies keep separate state.
inline const ast::Scope& observerBoundary(const ast::Scope& writer) {
    for (auto current = &writer; current; current = current->asSymbol().getParentScope()) {
        if (current->asSymbol().kind == ast::SymbolKind::GenerateBlock ||
            current->asSymbol().kind == ast::SymbolKind::InstanceBody)
            return *current;
    }
    return writer;
}

// One source edit applies to every physical writer of this storage at this
// lexical site. A reference valid only for one writer must not enter their
// union: its genvar can select an absent or different branch in another writer.
// Validate against the entire family before sharing the emitted spelling.
inline std::string scopeReference(const ast::Scope& target,
                                  const std::set<const ast::Scope*>& writers) {
    if (std::all_of(writers.begin(), writers.end(), [&](auto writer) {
            // Every observer boundary declares its own state interface. A
            // parent target is shadowed in a child generate boundary, even
            // though ordinary source lookup can find the original parent
            // storage there. Only the same declaration owner is local.
            return &observerBoundary(*writer) == &target;
        }))
        return "";
    auto valid = [&](const std::string& reference) {
        return std::all_of(writers.begin(), writers.end(), [&](auto writer) {
            return denotesScope(target, *writer, reference) &&
                   denotesScope(target, observerBoundary(*writer), reference);
        });
    };
    auto& symbol = target.asSymbol();
    const auto path = symbol.getHierarchicalPath();
    for (auto parent = &target; parent;
         parent = parent->asSymbol().getHierarchicalParent()) {
        auto body = parent->asSymbol().as_if<ast::InstanceBodySymbol>();
        if (!body)
            continue;
        auto prefix = body->getHierarchicalPath();
        if (path != prefix && !path.starts_with(prefix + "."))
            continue;
        auto anchor = "\\" + std::string(body->getDefinition().name) + " ";
        std::set<std::string> parametric;
        for (auto writer : writers)
            parametric.insert(parametricScopeSuffix(target, *parent, *writer,
                                                   observerBoundary(*writer)));
        for (auto& suffix : parametric) {
            auto candidate = anchor + suffix;
            if (valid(candidate))
                return candidate + ".";
        }
        auto candidate = anchor + path.substr(prefix.size());
        if (valid(candidate))
            return candidate + ".";
    }
    throw std::runtime_error("cannot render source subscriber scope: " + symbol.getHierarchicalPath());
}

} // namespace behave
