// Identify explicit value-event lists that use exactly the exported read ranges.
#pragma once

#include <algorithm>

#include "slang/ast/EvalContext.h"
#include "slang/ast/SemanticFacts.h"
#include "slang/ast/TimingControl.h"
#include "slang/ast/ValuePath.h"
#include "slang/ast/statements/MiscStatements.h"

namespace behave {

inline bool stationaryValueEvents(const slang::ast::TimingControl& timing,
                                  slang::ast::EvalContext& context) {
    using namespace slang::ast;
    if (auto list = timing.as_if<EventListControl>()) {
        return std::all_of(list->events.begin(), list->events.end(),
            [&](auto event) { return stationaryValueEvents(*event, context); });
    }
    auto event = timing.as_if<SignalEventControl>();
    if (!event || event->edge != EdgeKind::None || event->iffCondition)
        return false;
    // Changing a dependency of @(a+b) is not necessarily an event. Only a
    // compiler-bound, fully static value path equals its exported read range.
    // Dynamic selectors, arithmetic events, iff and edge controls keep their
    // original native scheduling; they are not widened to dependency changes.
    ValuePath path(event->expr, context);
    return !path.empty() && path.isFullyStatic() && path.rootSymbol() &&
           path.fullExpr == &event->expr;
}

inline bool stationaryValueEvents(const slang::ast::ProceduralBlockSymbol& block) {
    using namespace slang::ast;
    if (block.procedureKind != ProceduralBlockKind::Always)
        return false;
    auto timed = block.getBody().as_if<TimedStatement>();
    if (!timed)
        return false;
    EvalContext context(block);
    return stationaryValueEvents(timed->timing, context);
}

} // namespace behave
