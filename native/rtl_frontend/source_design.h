// The compiler's instantiated source closure, before runtime reachability.
#pragma once

#include <type_traits>

#include "slang/ast/ASTVisitor.h"
#include "slang/ast/Scope.h"

namespace behave {

// Slang retains uninstantiated module bodies and unselected generate arms for
// name checking. Its analysis manager skips these scopes, but ASTVisitor does
// not. Every supplemental source-fact walk must use the same compiler decision.
// Runtime if/loop bodies (including constant-dead ones) are still visited.
template<typename Derived>
struct SourceDesignVisitor : slang::ast::ASTVisitor<Derived, slang::ast::VisitFlags::AllGood> {
    template<typename Node> void visit(const Node& node) {
        if constexpr (std::is_base_of_v<slang::ast::Scope, Node>) {
            if (static_cast<const slang::ast::Scope&>(node).isUninstantiated())
                return;
        }
        slang::ast::ASTVisitor<Derived, slang::ast::VisitFlags::AllGood>::visit(node);
    }
};

} // namespace behave
