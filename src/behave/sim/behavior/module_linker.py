"""Hygienic linking for acyclic modules with fixed top-level bindings.

CPython's symbol table decides lexical ownership (including comprehensions and
closures). Imports resolve only against captured modules or registered concrete
libraries. Qualified library attributes can resolve to named imports; module
objects cannot escape as values. Module initializers may call fixed, already
bound functions; their original execution order is retained. Reflection,
rebinding and calls in signatures/decorators remain outside this subset.
"""

from __future__ import annotations

import ast
import builtins
import symtable
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from ..core.failure import CandidateFault
from ..core.refusal import Refusal, Refused
from .budgets import SOURCE_TEXT_CHARS
from .method_binding import class_member_name, method_kind
from .source_intrinsics import BUILTIN_NAMES, CONCRETE_MODULES, concrete_import_aliases
from .source_syntax import future_annotations_import, parsed_source

PREFIX = "_behave_link_"
REFLECTIVE = frozenset({"globals", "locals", "vars", "dir", "eval", "exec", "compile", "getattr",
                       "setattr", "delattr", "hasattr", "type", "id", "hash", "ascii", "repr", "str", "format", "super"})


def refuse(code, file, node=None, **detail):
    raise Refused(Refusal("behavior-bundle-" + code, "Python module semantics are outside the static-link subset",
                         file=file, line=getattr(node, "lineno", 0), **detail))


@dataclass(frozen=True)
class Binding:
    kind: str
    module: str
    name: str = ""


@dataclass
class Module:
    path: str
    tree: ast.Module
    table: symtable.SymbolTable
    bindings: dict[str, Binding]
    postponed_annotations: bool = False
    initialization_effects: bool = False


def static_value(node, *, annotation=False, call_target=None, initializer=False):
    """Definition syntax without calls, mutation, iteration or code objects.

    Annotations also admit qualified/subscripted types. Their expressions are
    retained for Python to evaluate, not executed or replaced with types here.
    Initializers additionally admit comprehensions and indexed table reads.
    Signature and class expressions retain their narrower definition syntax.
    """
    annotation_forms = (ast.Attribute, ast.Subscript, ast.Slice) if annotation else ()
    initializer_forms = ((ast.ListComp, ast.SetComp, ast.DictComp, ast.comprehension,
                          ast.Store, ast.Subscript, ast.Slice) if initializer else ())
    call_syntax = set()
    if call_target is not None:
        for part in ast.walk(node):
            if isinstance(part, ast.Call):
                if not call_target(part.func) or any(keyword.arg is None for keyword in part.keywords):
                    return False
                call_syntax.update(ast.walk(part.func))
                call_syntax.update(part.keywords)
                call_syntax.add(part)
    return all(isinstance(part, (ast.Constant, ast.Name, ast.Load, ast.Tuple, ast.List, ast.Set, ast.Dict,
                                ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
                                ast.operator, ast.unaryop, ast.boolop, ast.cmpop)
                         + annotation_forms + initializer_forms)
               or part in call_syntax
               for part in ast.walk(node))


def main_guard(node):
    """This branch cannot execute in the model's named module environment.

    Module rebinding of __name__ remains rejected by bind(); arbitrary dynamic
    namespaces are not inferred. Syntax inside the inactive branch was already
    checked by CPython before it is excluded from linking.
    """
    if not isinstance(node, ast.If) or node.orelse:
        return False
    test = node.test
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)):
        return False
    left, right = test.left, test.comparators[0]
    return any(isinstance(name, ast.Name) and name.id == "__name__"
               and isinstance(value, ast.Constant) and value.value == "__main__"
               for name, value in ((left, right), (right, left)))


def definition_expressions(node):
    """Expressions evaluated while defining a module, not later method bodies."""
    yield node
    children = ([node.args, *node.decorator_list, *([node.returns] if node.returns else [])]
                if isinstance(node, ast.FunctionDef) else ast.iter_child_nodes(node))
    for child in children:
        yield from definition_expressions(child)


class Linker:
    def __init__(self, bundle):
        self.bundle = bundle
        self.modules: dict[str, Module] = {}
        self.active: set[str] = set()
        self.order: list[str] = []
        self.externals: dict[Binding, str] = {}
        self.dependencies: dict[str, bytes] = {}
        self.selected: Binding | None = None
        self.names = {path: f"{PREFIX}{i}_" for i, path in enumerate(sorted(bundle.files))}

    def initializer_function(self, module, node):
        """Resolve an initializer call in the namespace bound at that statement.

        This is linking, not constant evaluation or a purity proof. The linked
        source executes the call once, at its original module position; later
        source admission owns state effects and exact read-only-table proofs.
        """
        def binding(expression):
            if isinstance(expression, ast.Name):
                return module.bindings.get(expression.id)
            if isinstance(expression, ast.Attribute):
                owner = binding(expression.value)
                if owner is not None and owner.kind == "module":
                    return self.modules[owner.module].bindings.get(expression.attr)
                if owner is not None and owner.kind == "external-module":
                    return self.external(owner.module, expression.attr, module.path, expression)
            return None

        target = binding(node)
        if target is None and isinstance(node, ast.Name) and node.id in BUILTIN_NAMES - REFLECTIVE:
            symbol = module.table.lookup(node.id)
            # A later module binding must not turn an early builtin call into
            # a forward reference when the module is renamed.
            return not (symbol.is_assigned() or symbol.is_imported())
        return target is not None and target.kind in {"function", "external"}

    def initializer_value(self, module, node):
        local = {part.id for generator in ast.walk(node) if isinstance(generator, ast.comprehension)
                 for part in ast.walk(generator.target) if isinstance(part, ast.Name)}
        return static_value(node, initializer=True, call_target=lambda target:
                            not (isinstance(target, ast.Name) and target.id in local)
                            and self.initializer_function(module, target))

    def initializer_statement(self, module, node):
        """Retain table construction and assertions, without executing them.

        Loops may introduce fresh iteration bindings and update already-bound
        module tables. They may not replace functions/imports or conditionally
        introduce module definitions. Python still owns iteration and failures
        inside the ordinary isolated load budget.
        """
        if isinstance(node, ast.Assert):
            return (self.initializer_value(module, node.test)
                    and (node.msg is None or self.initializer_value(module, node.msg)))
        if isinstance(node, ast.For) and not node.orelse:
            if not self.initializer_value(module, node.iter):
                return False
            if not isinstance(node.target, ast.Name):
                return False
            name = node.target.id
            self.bind(module, name, Binding("value", module.path, name), node.target)
            return all(self.initializer_statement(module, child) for child in node.body)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Subscript):
                return False
            base = target
            while isinstance(base, ast.Subscript):
                if not self.initializer_value(module, base.slice):
                    return False
                base = base.value
            binding = module.bindings.get(base.id) if isinstance(base, ast.Name) else None
            return (binding is not None and binding.kind == "value"
                    and binding.module == module.path and self.initializer_value(module, node.value))
        return False

    def module_path(self, name, file, node):
        if not name or any(not part.isidentifier() for part in name.split(".")):
            refuse("import-form", file, node)
        path = name.replace(".", "/")
        candidates = [p for p in (path + ".py", path + "/__init__.py") if p in self.bundle.files]
        if len(candidates) != 1:
            refuse("module-unresolved", file, node, module=name)
        for parent in PurePosixPath(path).parents:
            if str(parent) == ".":
                continue
            initializer = str(parent / "__init__.py")
            if str(parent) + ".py" in self.bundle.files:
                refuse("package-shadowed", file, node)
            if initializer in self.bundle.files:
                package = self.read(initializer)
                if any(not isinstance(n, ast.Pass) and not self.docstring(n) for n in package.tree.body):
                    refuse("package-initializer", initializer)
        return candidates[0]

    @staticmethod
    def docstring(node):
        return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and type(node.value.value) is str

    def imported_module(self, node, file):
        name = node.module or ""
        if node.level:
            parents = list(PurePosixPath(file).parts[:-1])
            if len(parents) < node.level:
                refuse("relative-import", file, node)
            name = ".".join((*parents[:len(parents) - node.level + 1], *(name.split(".") if name else ())))
        return name

    def external(self, module, name, file, node):
        if module not in CONCRETE_MODULES or (name is not None
                and (name.startswith("_") or name == "*")):
            refuse("import-form", file, node)
        if any(path.startswith(module + "/") for path in self.bundle.files):
            refuse("intrinsic-shadowed", file, node)
        if module + ".py" in self.bundle.files:
            if module != "fp_lib":
                refuse("intrinsic-shadowed", file, node)
            from . import LIBRARY_DIR
            if self.bundle.files["fp_lib.py"] != (LIBRARY_DIR / "fp_lib.py").read_bytes():
                refuse("intrinsic-shadowed", file, node)
        if module == "fp_lib":
            from . import LIBRARY_DIR
            self.dependencies["fp_lib.py"] = (LIBRARY_DIR / "fp_lib.py").read_bytes()
        if name is None:
            return Binding("external-module", module)
        binding = Binding("external", module, name)
        self.externals.setdefault(binding, f"{PREFIX}external_{len(self.externals)}")
        return binding

    def read(self, path):
        if path in self.active:
            refuse("import-cycle", path)
        if path in self.modules:
            return self.modules[path]
        try:
            text = self.bundle.files[path].decode("utf-8")
        except UnicodeError:
            raise CandidateFault(Refusal("behavior-source-not-utf8", "Python module is not UTF-8", file=path)) from None
        tree = parsed_source(text, path)
        try:
            table = symtable.symtable(text, path, "exec")
        except SyntaxError as exc:
            raise CandidateFault(Refusal("behavior-source-syntax-error", "Python symbol table rejected the source",
                                         file=path, line=exc.lineno, detail=exc.msg)) from None
        tree.body = [node for node in tree.body if not main_guard(node)]
        scopes = [table]
        while scopes:
            scope = scopes.pop()
            if any(name.startswith(PREFIX) for name in scope.get_identifiers()):
                refuse("reserved-name", path)
            scopes.extend(scope.get_children())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id.startswith("__") and node.id.endswith("__"):
                refuse("reserved-name", path, node)
            if isinstance(node, ast.arg) and node.arg.startswith("__") and node.arg.endswith("__"):
                refuse("reserved-name", path, node)
            if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
                refuse("reflection", path, node)
        module = Module(path, tree, table, {})
        self.active.add(path)
        evaluated_names = set()
        initialization_call = False
        definitions_started = False
        for node in tree.body:
            if isinstance(node, ast.Pass) or self.docstring(node):
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    if not future_annotations_import(node):
                        refuse("import-form", path, node)
                    module.postponed_annotations = True
                    continue
                aliases = node.names if isinstance(node, ast.Import) else concrete_import_aliases(node)
                imported_names = {alias.asname or alias.name for alias in aliases}
                # Flattening acyclic modules can move their initialization before
                # these declarations. Function bodies have not run yet, but an
                # earlier initializer call or definition-time read could observe
                # the moved binding. Do not silently change those executions.
                if initialization_call or imported_names & evaluated_names:
                    refuse("late-import-effects", path, node)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "." in alias.name and alias.asname is None:
                            refuse("import-form", path, node)
                        if alias.name in CONCRETE_MODULES:
                            binding = self.external(alias.name, None, path, node)
                        else:
                            target = self.module_path(alias.name, path, node)
                            dependency = self.read(target)
                            if definitions_started and dependency.initialization_effects:
                                refuse("late-import-effects", path, node)
                            module.initialization_effects |= dependency.initialization_effects
                            binding = Binding("module", target)
                        self.bind(module, alias.asname or alias.name, binding, node)
                else:
                    target_name = self.imported_module(node, path)
                    for alias in aliases:
                        if alias.name == "*":
                            refuse("import-form", path, node)
                        if target_name in CONCRETE_MODULES:
                            binding = self.external(target_name, alias.name, path, node)
                        else:
                            target = self.read(self.module_path(target_name, path, node))
                            if definitions_started and target.initialization_effects:
                                refuse("late-import-effects", path, node)
                            module.initialization_effects |= target.initialization_effects
                            binding = target.bindings.get(alias.name)
                            if binding is None:
                                refuse("export-unresolved", path, node, name=alias.name)
                        self.bind(module, alias.asname or alias.name, binding, node)
                continue
            definitions_started = True
            for expression in definition_expressions(node):
                if isinstance(expression, ast.Name) and isinstance(expression.ctx, ast.Load):
                    evaluated_names.add(expression.id)
                initialization_call |= isinstance(expression, ast.Call)
            module.initialization_effects |= initialization_call
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                # Fixed callable aliases denote the same binding, not a new
                # definition. Only an already-bound callable may be aliased;
                # later rebinding remains outside this subset.
                target = (module.bindings.get(node.value.id)
                          if isinstance(node.value, ast.Name) else None)
                if target is not None and target.kind in ("class", "function", "external"):
                    self.bind(module, node.targets[0].id, target, node)
                    continue
                if not self.initializer_value(module, node.value):
                    refuse("module-effects", path, node)
                name, kind = node.targets[0].id, "value"
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                # Annotation-only statements do not bind a module value.
                if node.value is None:
                    continue
                if not self.initializer_value(module, node.value):
                    refuse("module-effects", path, node)
                name, kind = node.target.id, "value"
            elif isinstance(node, ast.FunctionDef):
                name, kind = node.name, "function"
            elif isinstance(node, ast.ClassDef):
                name, kind = node.name, "class"
            elif self.initializer_statement(module, node):
                initialization_call = module.initialization_effects = True
                continue
            else:
                refuse("module-effects", path, node)
            self.bind(module, name, Binding(kind, path, name), node)
        self.active.remove(path)
        self.modules[path] = module
        self.order.append(path)
        return module

    def bind(self, module, name, binding, node):
        if name in module.bindings or name.startswith((PREFIX, "__")):
            refuse("binding-redefined", module.path, node, name=name)
        module.bindings[name] = binding

    def linked_name(self, binding):
        if binding == self.selected:
            return self.bundle.model
        if binding.kind == "external":
            return self.externals[binding]
        return self.names[binding.module] + binding.name


class Rewrite(ast.NodeTransformer):
    def __init__(self, linker, module, *, definitions_only=False):
        self.linker, self.module = linker, module
        self.table = module.table
        self.scopes = [module.table]
        self.used_tables: set[symtable.SymbolTable] = set()
        self.class_bound: set[str] | None = None
        self.call_target: ast.expr | None = None
        self.in_annotation = False
        self.definitions_only = definitions_only

    def visit(self, node):
        if isinstance(node, (ast.Global, ast.AsyncFunctionDef, ast.Await, ast.Try,
                             ast.TryStar, ast.Yield, ast.YieldFrom)):
            refuse("dynamic-namespace", self.module.path, node)
        return super().visit(node)

    def private_name(self, name, node=None, *, attribute=False):
        """Keep Python's original private key when linking renames its class.

        CPython's symbol table already uses this key. Emitting it explicitly
        also preserves direct accesses to an original mangled member name;
        allowing the renamed class to mangle it again would change behavior.
        """
        if not name.startswith("__") or name.endswith("__"):
            return name
        owner = next((scope.get_name() for scope in reversed(self.scopes)
                      if scope.get_type() == "class"), None)
        if owner is None:
            refuse("reflection" if attribute else "reserved-name", self.module.path, node)
        if not owner.lstrip("_"):
            # Python leaves these names unmangled. A renamed class would not;
            # preserving that exceptional key needs a non-mangling class ABI.
            refuse("private-class-name", self.module.path, node)
        return class_member_name(owner, name)

    def binding(self, name):
        name = self.private_name(name)
        symbol = self.table.lookup(name)
        is_global = self.table.get_type() == "module" or symbol.is_global()
        if self.class_bound is not None and symbol.is_local() and name not in self.class_bound:
            is_global = True  # class LOAD_NAME falls through to its module
        return self.module.bindings.get(name) if is_global else None

    def reference(self, node):
        if isinstance(node, ast.Name):
            return self.binding(node.id)
        if isinstance(node, ast.Attribute):
            owner = self.reference(node.value)
            if owner is not None and owner.kind == "external-module":
                return self.linker.external(owner.module, node.attr, self.module.path, node)
            if owner is not None and owner.kind == "module":
                binding = self.linker.modules[owner.module].bindings.get(node.attr)
                if binding is None:
                    refuse("export-unresolved", self.module.path, node, name=node.attr)
                return binding
        return None

    def renamed(self, node, binding):
        # Functions cannot escape into reflection/formatting; constants may.
        # External objects retain their original identity, including their names.
        if binding.kind in ("module", "external-module") or (binding.kind in ("function", "class")
                and self.call_target is not node
                and not (self.in_annotation and binding.kind == "class")):
            refuse("object-escape", self.module.path, node)
        if not isinstance(node.ctx, ast.Load) and self.table.get_type() != "module":
            refuse("module-write", self.module.path, node)
        return ast.copy_location(ast.Name(id=self.linker.linked_name(binding), ctx=node.ctx), node)

    def visit_Name(self, node):
        node.id = self.private_name(node.id, node)
        binding = self.binding(node.id)
        if binding is not None:
            return self.renamed(node, binding)
        # A nested function's qualname also contains its renamed outer function.
        # Track lexical owners rather than only checking module-level escapes.
        for scope in reversed(self.scopes):
            if scope.get_type() == "class" and scope is not self.table:
                continue
            if node.id in scope.get_identifiers():
                symbol = scope.lookup(node.id)
                if symbol.is_local():
                    if symbol.is_namespace() and self.call_target is not node:
                        refuse("object-escape", self.module.path, node)
                    break
                if symbol.is_global():
                    break
        if self.table.lookup(node.id).is_global() and (
                (node.id in REFLECTIVE and not self.in_annotation)
                or node.id.startswith("_") or not hasattr(builtins, node.id)):
            refuse("global-unresolved", self.module.path, node, name=node.id)
        return node

    def visit_Attribute(self, node):
        node.attr = self.private_name(node.attr, node, attribute=True)
        binding = self.reference(node)
        if binding is not None:
            if not isinstance(node.ctx, ast.Load):
                refuse("module-write", self.module.path, node)
            return self.renamed(node, binding)
        # Only direct global calls may reference a function object.
        saved, self.call_target = self.call_target, None
        owner = self.reference(node.value)
        if saved is node and owner is not None and owner.kind == "class":
            self.call_target = node.value  # Class.helper(...), not a class escape
        node.value = self.visit(node.value)
        self.call_target = saved
        return node

    def visit_Call(self, node):
        target = self.reference(node.func)
        field = target == Binding("external", "dataclasses", "field")
        saved, self.call_target = self.call_target, node.func
        node.func = self.visit(node.func)
        self.call_target = None
        node.args = [self.visit(arg) for arg in node.args]
        for keyword in node.keywords:
            # A declared factory is invoked by the standard constructor. Keep
            # its callable binding, as for a direct call, without executing it.
            self.call_target = keyword.value if field and keyword.arg == "default_factory" else None
            keyword.value = self.visit(keyword.value)
        self.call_target = saved
        return node

    def visit_Import(self, node):
        if self.table.get_type() != "module":
            refuse("local-import", self.module.path, node)

    visit_ImportFrom = visit_Import

    def visit_Assign(self, node):
        if self.table.get_type() == "module" and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            binding = self.module.bindings.get(node.targets[0].id)
            if binding is not None and binding.kind in ("class", "function", "external"):
                # Every reference is rewritten to the defining binding. Keeping
                # the alias assignment would rebind that normalized definition.
                return None
        return self.generic_visit(node)

    def annotation(self, node):
        if node is None:
            return None
        if self.module.postponed_annotations:
            # A future import is module-local. Retain its original spelling as
            # metadata, rather than applying that future flag to every module.
            return ast.copy_location(ast.Constant(ast.unparse(node)), node)
        if not static_value(node, annotation=True):
            refuse("definition-effects", self.module.path, node)
        previous, self.in_annotation = self.in_annotation, True
        try:
            return self.visit(node)
        finally:
            self.in_annotation = previous

    def visit_AnnAssign(self, node):
        if node.value is not None:
            node.value = self.visit(node.value)
        if self.table.get_type() == "module" and node.value is None:
            # This target only names an annotation entry, not a Store. It need
            # not have a runtime binding and must not shadow an imported value.
            node.target = ast.copy_location(ast.Name(
                id=self.linker.names[self.module.path] + node.target.id,
                ctx=ast.Store()), node.target)
        elif self.table.get_type() != "class":
            node.target = self.visit(node.target)
        elif isinstance(node.target, ast.Name):
            node.target.id = self.private_name(node.target.id, node.target)
        if self.class_bound is not None and node.value is not None:
            self.class_bound.add(node.target.id)
        if self.table.get_type() != "function":
            node.annotation = self.annotation(node.annotation)
        # Function-local annotations are not evaluated. Keep the AnnAssign,
        # including annotation-only locals and attribute/subscript targets:
        # deleting it would change lexical binding or target evaluation.
        return node

    def enter(self, node, name, body):
        child = next((child for child in self.table.get_children() if child.get_name() == name
                      and child.get_lineno() == node.lineno and child not in self.used_tables), None)
        if child is None:
            raise RuntimeError("AST and Python symbol table scopes disagree")
        self.used_tables.add(child)
        previous, self.table = self.table, child
        self.scopes.append(child)
        bound, self.class_bound = self.class_bound, set() if child.get_type() == "class" else None
        try:
            return body()
        finally:
            self.scopes.pop()
            self.table, self.class_bound = previous, bound

    def visit_FunctionDef(self, node):
        if getattr(node, "type_params", ()):
            refuse("definition-effects", self.module.path, node)
        if node.decorator_list:
            kind = method_kind(node)
            decorator = node.decorator_list[0]
            if (self.class_bound is None or kind is None
                    or (isinstance(decorator, ast.Name)
                        and (decorator.id in self.class_bound or self.binding(decorator.id) is not None))
                    or (isinstance(decorator, ast.Attribute)
                        and decorator.value.id not in self.class_bound)):
                refuse("definition-effects", self.module.path, node)
            if isinstance(decorator, ast.Attribute):
                # This is the preceding descriptor in this same class, not a
                # function escaping through an arbitrary module expression.
                decorator.value.id = self.private_name(decorator.value.id, decorator.value)
            else:
                node.decorator_list = [self.visit(item) for item in node.decorator_list]
        defaults = [*node.args.defaults, *(n for n in node.args.kw_defaults if n is not None)]
        if not all(static_value(default) for default in defaults):
            refuse("definition-effects", self.module.path, node)
        node.args.defaults = [self.visit(n) for n in node.args.defaults]
        node.args.kw_defaults = [None if n is None else self.visit(n) for n in node.args.kw_defaults]
        # Signatures evaluate in the enclosing scope, not the function scope.
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                    *(a for a in (node.args.vararg, node.args.kwarg) if a)):
            arg.arg = self.private_name(arg.arg, arg)
            arg.annotation = self.annotation(arg.annotation)
        node.returns = self.annotation(node.returns)
        original = node.name
        binding = self.module.bindings.get(original) if self.table.get_type() == "module" else None
        if not self.definitions_only:
            node.body = self.enter(node, original, lambda: [self.visit(n) for n in node.body])
        if binding is not None:
            node.name = self.linker.linked_name(binding)
        else:
            node.name = self.private_name(original, node)
        if self.class_bound is not None:
            self.class_bound.add(node.name)
        return node

    def visit_ClassDef(self, node):
        from .class_binding import dataclass_declaration

        def library(expression):
            binding = self.reference(expression)
            return (binding.module, binding.name) if binding is not None and binding.kind == "external" else None

        dataclass = dataclass_declaration(node, library)
        plain_object = (len(node.bases) == 1 and isinstance(node.bases[0], ast.Name)
                        and node.bases[0].id == "object" and "object" not in self.module.bindings)
        if (self.table.get_type() != "module" or (node.bases and not plain_object)
                or node.keywords or (node.decorator_list and not dataclass)):
            refuse("class-form", self.module.path, node)
        node.decorator_list = [self.visit(decorator) for decorator in node.decorator_list]

        def field_factory(expression):
            return dataclass and library(expression) == ("dataclasses", "field")

        def body():
            result = []
            for child in node.body:
                if isinstance(child, ast.Assign) and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name):
                    if not static_value(child.value, call_target=field_factory):
                        refuse("definition-effects", self.module.path, child)
                    child.value = self.visit(child.value)
                    child.targets[0].id = self.private_name(child.targets[0].id, child.targets[0])
                    self.class_bound.add(child.targets[0].id)
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    if child.value is not None and not static_value(child.value, call_target=field_factory):
                        refuse("definition-effects", self.module.path, child)
                    child = self.visit(child)
                elif isinstance(child, ast.FunctionDef):
                    child = self.visit(child)
                elif not isinstance(child, ast.Pass) and not self.linker.docstring(child):
                    refuse("class-form", self.module.path, child)
                result.append(child)
            return result
        original = node.name
        node.body = self.enter(node, original, body)
        node.name = self.linker.linked_name(self.module.bindings[original])
        return node

    def visit_Lambda(self, node):
        refuse("definition-effects", self.module.path, node)

    def comprehension(self, node, name):
        node.generators[0].iter = self.visit(node.generators[0].iter)
        def body():
            for i, generator in enumerate(node.generators):
                if i:
                    generator.iter = self.visit(generator.iter)
                generator.target = self.visit(generator.target)
                generator.ifs = [self.visit(test) for test in generator.ifs]
            if isinstance(node, ast.DictComp):
                node.key, node.value = self.visit(node.key), self.visit(node.value)
            else:
                node.elt = self.visit(node.elt)
            return node
        return self.enter(node, name, body)

    def visit_ListComp(self, node):
        return self.comprehension(node, "listcomp")

    def visit_SetComp(self, node):
        return self.comprehension(node, "setcomp")

    def visit_DictComp(self, node):
        return self.comprehension(node, "dictcomp")

    def visit_GeneratorExp(self, node):
        # The outer iterable is evaluated in its enclosing scope immediately;
        # the body/filters stay inside CPython's lazy generator scope.
        return self.comprehension(node, "genexpr")

def link(bundle):
    from .source_bundle import PreparedBehaviorSource

    linker = Linker(bundle)
    entry = linker.read(bundle.entry)
    selected = entry.bindings.get(bundle.model)
    linker.selected = selected if selected is not None and selected.kind == "class" else None
    # With no module-initialization calls, deferred function bodies cannot
    # create or replace the selected entry. Still validate every definition,
    # import, decorator and class body using the normal namespace rules.
    # An initializer call instead needs the full body validation below.
    definitions_only = linker.selected is None and not any(
        module.initialization_effects for module in linker.modules.values())
    body = []
    for path in linker.order:
        module = linker.modules[path]
        body.extend(Rewrite(linker, module, definitions_only=definitions_only).visit(module.tree).body)
    # Only after the closed namespace has been validated can a missing or
    # non-class selection be attributed to the submitter. Dynamic factories,
    # decorators and imports still get their own capability diagnosis above.
    if linker.selected is None:
        raise CandidateFault(Refusal(
            "behavior-entry-not-class", "the selected Behavior entry does not name a class",
            file=bundle.entry, model=bundle.model,
            binding=None if selected is None else selected.kind))
    if not any(module.initialization_effects for module in linker.modules.values()):
        from .source_entry import plain_namespace_missing_process

        if plain_namespace_missing_process(body, bundle.model):
            raise CandidateFault(Refusal(
                "behavior-entry-method-missing",
                "the selected Behavior class supplies no process, process_slot or clock-domain entry",
                file=bundle.entry, model=bundle.model, required="process"))
    imports = [ast.ImportFrom(module=binding.module,
                              names=[ast.alias(name=binding.name, asname=name)], level=0)
               for binding, name in linker.externals.items()]
    source = f"# behave.behavior-source-link/4 {bundle.identity.sha256}\n" + ast.unparse(
        ast.fix_missing_locations(ast.Module(body=imports + body, type_ignores=[]))) + "\n"
    if len(source) > SOURCE_TEXT_CHARS:
        refuse("source-limit", bundle.entry)
    symbols = tuple((path, name, linker.linked_name(binding)) for path in linker.order
                    for name, binding in linker.modules[path].bindings.items()
                    if binding.kind not in ("module", "external-module"))
    return PreparedBehaviorSource(source, bundle.identity, tuple(linker.order), symbols,
                                  MappingProxyType(linker.dependencies), model=bundle.model)
