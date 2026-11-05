"""Technology data for the shared synthesis/placement/STA flow.

Public constraints use ns and pF. RC commands retain the platform's native
Liberty units; only the public output load is converted at the STA boundary.
"""

from dataclasses import dataclass

from behave.core.paths import repo_root


@dataclass(frozen=True)
class Technology:
    name: str
    profile: str
    library_prefix: str
    capacitance_unit: str
    liberty_path: str
    tech_lef_path: str
    cells_lef_path: str
    rc: str
    placement: str
    tie_high: tuple[str, str]
    tie_low: tuple[str, str]

    @property
    def liberty(self):
        return repo_root() / self.liberty_path

    @property
    def lef_mounts(self):
        from behave.isolation import read_only
        return tuple(read_only(str(repo_root() / path), target) for path, target in (
            (self.tech_lef_path, '/pdk/technology.lef'),
            (self.cells_lef_path, '/pdk/cells.lef')))

    def load_from_pf(self, value):
        return value * (1000 if self.capacitance_unit == 'fF' else 1)

    def validate_library(self, description):
        if (not description['library'].startswith(self.library_prefix)
                or description['capacitance_unit'] != self.capacitance_unit):
            raise ValueError(f'Liberty does not match technology profile {self.name}')


def technology(name='sky130hd'):
    if name == 'sky130hd':
        from .sky130 import PROFILE, RC, PLACEMENT
        return Technology(name, PROFILE, 'sky130_fd_sc_hd__', 'pF',
            'external/ppa-activity/sky130.lib',
            'external/ppa-netlist-repair/sky130_tech.tlef',
            'external/ppa-netlist-repair/sky130_cells.lef', RC, PLACEMENT,
            ('sky130_fd_sc_hd__conb_1', 'HI'), ('sky130_fd_sc_hd__conb_1', 'LO'))
    if name == 'nangate45':
        from .nangate45 import PROFILE, RC, PLACEMENT
        root = 'external/ppa-platforms/nangate45/'
        return Technology(name, PROFILE, 'NangateOpenCellLibrary', 'fF',
            root + 'lib/NangateOpenCellLibrary_typical.lib',
            root + 'lef/NangateOpenCellLibrary.tech.lef',
            root + 'lef/NangateOpenCellLibrary.macro.mod.lef', RC, PLACEMENT,
            ('LOGIC1_X1', 'Z'), ('LOGIC0_X1', 'Z'))
    raise ValueError(f'unknown PPA technology: {name}')
