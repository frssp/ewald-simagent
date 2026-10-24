"""A made-up sim-agent ``vasp_workfunction`` run with known work functions: two slab statics,
Cu-like slabs 15 A thick in a 30 A cell, the planar average -8 eV inside and flat in vacuum."""
import numpy as np

from .band_offset_data import _planes, planar_json, vasprun

PHI = {"111_t1": 4.9, "100_t1": 4.6}
EF = {"111_t1": -0.4, "100_t1": 0.2}


def files() -> dict:
    out = {}
    eig = np.array([[-1.0, 0.0, 1.0]])
    for surface, phi in PHI.items():
        pos, types = _planes(np.arange(5.0, 20.0, 2.0), ["Cu", "Cu"])
        z = np.arange(300) * 30.0 / 300
        vac = EF[surface] + phi
        values = np.where((z > 4.0) & (z < 20.0), -8.0, vac) + 0.0 * z
        out[f"slab_{surface}_static"] = {
            "vasprun.xml": vasprun(np.diag([4.0, 4.0, 30.0]), pos, types, eig, (eig < EF[surface]).astype(float),
                                   EF[surface]),
            "locpot_planar_avg.json": planar_json(values)}
    return out
