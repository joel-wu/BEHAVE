"""OpenSTA-compatible type view of an already mapped, bit-connected netlist.

Runs inside the EDA namespace, never on RTL expressions. Yosys JSON encodes
connections as exact bit identifiers/constants; signedness on net/port names is
then only display metadata. Cell types, parameters, connections and bit order
must remain untouched. The original mapped.json remains the area authority.
"""

import copy
import json
from pathlib import Path


def scope_metadata(cell: dict) -> bool:
    """Yosys flatten provenance, with no electrical pins or connectivity."""
    return (cell.get("type") == "$scopeinfo"
            and cell.get("connections") == {}
            and cell.get("port_directions", {}) == {})


def structural_view(design: dict, library: dict) -> dict:
    result = copy.deepcopy(design)
    trusted = library["modules"]
    for name, module in result["modules"].items():
        if name in trusted:
            if module != trusted[name]:
                raise ValueError("mapped design changed a library module")
            continue
        if module.get("processes") or module.get("memories"):
            raise ValueError("STA type view requires a fully mapped design")
        for key, cell in list(module.get("cells", {}).items()):
            if scope_metadata(cell):
                del module["cells"][key]
                continue
            if cell["type"] not in trusted:
                raise ValueError("STA type view cannot reinterpret unmapped cells")
        for collection in ("ports", "netnames"):
            for net in module.get(collection, {}).values():
                net.pop("signed", None)
    return result


if __name__ == "__main__":
    design = json.loads(Path("mapped.json").read_text())
    library = json.loads(Path("library.json").read_text())
    Path("sta.json").write_text(json.dumps(structural_view(design, library)))
    print("sta.json")
