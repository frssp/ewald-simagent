"""A made-up sim-agent ``vasp_band_offset`` run with a known answer, for the lineup analysis.

A "TiN" substrate (Ti+N planes) and a "TiO2" film (Ti+O+O planes), 2 A apart, in a 4 x 4 x 40 A
superlattice; two bulk references of 2 planes each. Planar averages oscillate with the 2 A
plane period around -5 eV (substrate) and -4.2 eV (film), -3 eV and -1 eV in the references.
The substrate reference is a metal (a band crosses E_F); the film reference has VBM 2, CBM 4.
With the interface E_F at 0.5 eV the film's VBM lands at 2 + (-4.2 - (-1)) = -1.2 eV, so
Phi_Bp = 1.7 eV and Phi_Bn = 0.3 eV.
"""
import json

import numpy as np

A, C = 4.0, 40.0
PHI_BP, PHI_BN = 1.7, 0.3


def _planes(z_values, names):
    pos, types = [], []
    for z in z_values:
        for i, t in enumerate(names):
            pos.append([A * i / len(names), A * (i % 2) / 2, z])
            types.append(t)
    return np.array(pos), types


def _f(x):
    return f"{x:16.8f}"


def vasprun(lattice, positions, types, eig, occ, efermi) -> bytes:
    """A minimal vasprun.xml: atoms, one ionic step with eigenvalues, E_F."""
    frac = np.asarray(positions) @ np.linalg.inv(lattice)
    names = list(dict.fromkeys(types))
    out = ['<?xml version="1.0" encoding="ISO-8859-1"?>', "<modeling>", " <atominfo>",
           f"  <atoms>{len(types):8d} </atoms>", f"  <types>{len(names):8d} </types>", '  <array name="atoms" >',
           '   <dimension dim="1">ion</dimension>', '   <field type="string">element</field>',
           '   <field type="int">atomtype</field>', "   <set>"]
    out += [f"    <rc><c>{t:2s}</c><c>{names.index(t) + 1:4d}</c></rc>" for t in types]
    out += ["   </set>", "  </array>", " </atominfo>"]

    def structure(name=None):
        lines = [f'  <structure name="{name}" >' if name else "  <structure>", "   <crystal>",
                 '    <varray name="basis" >']
        lines += [f"     <v>{''.join(_f(c) for c in v)} </v>" for v in lattice]
        lines += ["    </varray>", "   </crystal>", '   <varray name="positions" >']
        lines += [f"    <v>{''.join(_f(c) for c in v)} </v>" for v in frac]
        return lines + ["   </varray>", "  </structure>"]

    out += structure("initialpos")
    out += [" <calculation>", "  <scstep>", "   <energy>", '    <i name="e_fr_energy">    -10.00000000 </i>',
            "   </energy>", "  </scstep>"] + structure()
    out += ["  <energy>", '   <i name="e_fr_energy">    -10.00000000 </i>', "  </energy>", "  <eigenvalues>",
            "   <array>", '    <dimension dim="1">band</dimension>', '    <dimension dim="2">kpoint</dimension>',
            '    <dimension dim="3">spin</dimension>', "    <field>eigene</field>", "    <field>occ</field>",
            "    <set>", '     <set comment="spin 1">']
    for k in range(eig.shape[0]):
        out.append(f'      <set comment="kpoint {k + 1}">')
        out += [f"       <r>{_f(eig[k, b])}{_f(occ[k, b])} </r>" for b in range(eig.shape[1])]
        out.append("      </set>")
    out += ["     </set>", "    </set>", "   </array>", "  </eigenvalues>", "  <dos>",
            f'   <i name="efermi">{_f(efermi)} </i>', "  </dos>", " </calculation>"]
    out += structure("finalpos") + ["</modeling>"]
    return ("\n".join(out) + "\n").encode()


def planar_json(values) -> bytes:
    values = np.asarray(values, dtype=float)
    return json.dumps({"grid": [10, 10, len(values)], "planar_avg_z": values.tolist()}).encode()


def _wave(n, length, mean):
    z = np.arange(n) * length / n
    return mean + 1.5 * np.cos(2 * np.pi * z / 2.0)


def files() -> dict:
    """``{step: {file name: bytes}}`` for interface_static, ref_substrate_static, ref_film_static."""
    sub_pos, sub_types = _planes(np.arange(1.0, 19.0, 2.0), ["Ti", "N"])
    film_pos, film_types = _planes(np.arange(21.0, 39.0, 2.0), ["Ti", "O", "O"])
    iface_pos, iface_types = np.vstack([sub_pos, film_pos]), sub_types + film_types
    n = 400
    z = np.arange(n) * C / n
    in_film = 0.5 * (np.tanh((z - 20.0) / 0.6) - np.tanh((z - 39.5) / 0.6))
    iface_planar = -5.0 + 0.8 * in_film + 1.5 * np.cos(2 * np.pi * z / 2.0)
    metal_eig = np.array([[-1.0, 0.0, 1.0], [-0.5, 2.0, 3.0]])    # band 2 crosses E_F = 1.0
    metal_occ = (metal_eig < 1.0).astype(float)
    semi_eig = np.array([[1.0, 2.0, 4.0, 5.0], [0.5, 1.5, 4.5, 5.5]])  # VBM 2, CBM 4
    semi_occ = (semi_eig < 3.0).astype(float)
    sub_ref_pos, sub_ref_types = _planes([1.0, 3.0], ["Ti", "N"])
    film_ref_pos, film_ref_types = _planes([1.0, 3.0], ["Ti", "O", "O"])
    ref_lattice = np.diag([A, A, 4.0])
    return {
        "interface_static": {
            "vasprun.xml": vasprun(np.diag([A, A, C]), iface_pos, iface_types, metal_eig, metal_occ, 0.5),
            "locpot_planar_avg.json": planar_json(iface_planar)},
        "ref_substrate_static": {
            "vasprun.xml": vasprun(ref_lattice, sub_ref_pos, sub_ref_types, metal_eig, metal_occ, 1.0),
            "locpot_planar_avg.json": planar_json(_wave(40, 4.0, -3.0))},
        "ref_film_static": {
            "vasprun.xml": vasprun(ref_lattice, film_ref_pos, film_ref_types, semi_eig, semi_occ, 3.0),
            "locpot_planar_avg.json": planar_json(_wave(40, 4.0, -1.0))},
    }
