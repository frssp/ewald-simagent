"""Analyses of sim-agent workflows, as notebook cells (:class:`ewald.workflow.Analysis`).

One recipe per sim-agent template. A recipe names the steps it reads by the template's task
names, checks that they are there and finished, and writes cells that load those steps' files
and compute the result with EWALD's own kernels (:mod:`ewald.potential`), in the open: the
notebook is the record, and nothing is taken from sim-agent's ``finalize_workgraph`` output,
which the API does not serve. Task names are sim-agent's, quoted with the file and constant
they come from; a workflow whose steps do not match gets the analysis with a ``reason``
instead of a guess.

To add a template: write ``def _my_template(wf) -> list[Analysis]`` and list it in
:data:`RECIPES`.
"""
from __future__ import annotations

import textwrap

from ewald.plugin_api import Analysis

PLANAR_JSON = "locpot_planar_avg.json"

#: vasp_band_offset's statics: frssp/sim_agent src/sim_agent/workgraphs/templates/vasp_band_offset.py
#: (INTERFACE_STATIC, REF_TASKS), the names its finalize reads too.
BAND_OFFSET_STEPS = {"interface": "interface_static", "substrate": "ref_substrate_static",
                     "film": "ref_film_static"}


def _cell(text: str) -> str:
    return textwrap.dedent(text).strip()


def _steps(wf) -> dict:
    return {s.name: s for _, s in wf.walk()}


def _missing(wf, wanted: dict, files=()) -> str:
    """Why the steps ``wanted`` ({role: step name}) cannot be analysed, or ""."""
    steps = _steps(wf)
    absent = [name for name in wanted.values() if name not in steps]
    if absent:
        return f"steps {', '.join(absent)} are not in this workflow"
    failed = [f"{name} ({steps[name].mark() or steps[name].state})" for name in wanted.values()
              if steps[name].state != "finished" or steps[name].failed]
    if failed:
        return f"steps {', '.join(failed)} did not finish cleanly"
    for name in wanted.values():
        have = {o.name for o in steps[name].outputs}
        lacking = [f for f in files if f not in have]
        if lacking:
            return f"{name} has no {', '.join(lacking)} (was it run with LVHAR?)"
    return ""


def _band_offset(wf) -> list[Analysis]:
    title = "Potential lineup: band offset / Schottky barrier"
    why = _missing(wf, BAND_OFFSET_STEPS, files=(PLANAR_JSON, "vasprun.xml"))
    if why:
        return [Analysis("lineup", title, reason=why)]
    steps = _steps(wf)
    path = {role: steps[name].path for role, name in BAND_OFFSET_STEPS.items()}
    if not all(path.values()):
        return [Analysis("lineup", title, reason="a static has no vasprun.xml to read")]
    fetch = _cell(f"""
        import numpy as np
        from ewald import potential as pot
        from ewald_simagent import local_file

        steps = {path!r}
        runs = {{role: load(path) for role, path in steps.items()}}
        frames = {{role: run.compute(run.num_frames - 1) for role, run in runs.items()}}
        planar = {{role: pot.read_planar_json(local_file(steps[role] + '/{PLANAR_JSON}'), frames[role])
                  for role in steps}}
        """)
    geometry = _cell("""
        # which atoms are substrate and film (plane composition against the two bulk references),
        # and each side's repeat distance along the normal, read from its layer in the interface
        # (the macroscopic-average windows; the bulk references are primitive cells in any orientation)
        sides = pot.split_sides(frames['interface'], frames['substrate'], frames['film'])
        periods = {side: pot.layer_period(frames['interface'], sides[side]) for side in ('substrate', 'film')}
        z = np.mod(frames['interface'].cell.to_fractional(frames['interface'].positions)[:, 2], 1.0)
        lineup = pot.potential_lineup(planar['interface'], z[sides['substrate']], z[sides['film']],
                                      periods['substrate'], periods['film'])
        {'ok': lineup['ok'], 'reason': lineup['reason'], 'periods_A': periods,
         **{side: {k: p.get(k) for k in ('mean_eV', 'drift_eV')} for side, p in lineup['plateaus'].items()}}
        """)
    align = _cell("""
        # band edges of each bulk reference moved by its plateau (V_plateau - V_bulk); E_F of the interface
        edges = {side: pot.bulk_edges(frames[side], planar[side]) for side in ('substrate', 'film')}
        alignment = pot.band_alignment(lineup, edges['substrate'], edges['film'],
                                       interface_fermi=frames['interface'].attributes.get('Fermi Energy'))
        reference_gap = None   # a gap you trust (experiment, hybrid), eV: adds the scissor row (approx.)
        scissor = pot.scissor_schottky(alignment, reference_gap) if reference_gap else {}
        names = {side: frames[side].to_ase().get_chemical_formula(empirical=True) for side in ('substrate', 'film')}
        band_offset = pot.lineup_table(planar['interface'], lineup, alignment, scissor, names)
        band_offset.attributes['result']
        """)
    return [Analysis("lineup", title, [fetch, geometry, align], result="band_offset")]


def _slab_statics(wf) -> dict:
    """``{surface: step}`` of the slab statics, named ``slab_<hkl>_<term>_static``
    (frssp/sim_agent workgraphs/templates/vasp_workfunction.py and
    vasp_polycrystalline_workfunction.py; their finalize walks the same names)."""
    return {name[len("slab_"):-len("_static")]: step for name, step in _steps(wf).items()
            if name.startswith("slab_") and name.endswith("_static")}


def _work_function(wf) -> list[Analysis]:
    title = "Work function per surface"
    statics = _slab_statics(wf)
    if not statics:
        return [Analysis("work_function", title, reason="no slab_*_static steps in this workflow")]
    usable = {s: st for s, st in statics.items()
              if st.state == "finished" and not st.failed and st.path
              and {PLANAR_JSON, "vasprun.xml"} <= {o.name for o in st.outputs}}
    skipped = sorted(set(statics) - set(usable))
    if not usable:
        return [Analysis("work_function", title, reason=f"no slab static finished with {PLANAR_JSON} and "
                         f"vasprun.xml (was it run with LVHAR?): {', '.join(skipped)}")]
    paths = {s: st.path for s, st in usable.items()}
    fetch = _cell(f"""
        import numpy as np
        from ewald import potential as pot
        from ewald.verbs import Table
        from ewald_simagent import local_file

        slabs = {paths!r}
        skipped = {skipped!r}   # slab statics that did not finish with a planar average
        runs = {{s: load(path) for s, path in slabs.items()}}
        frames = {{s: run.compute(run.num_frames - 1) for s, run in runs.items()}}
        planar = {{s: pot.read_planar_json(local_file(slabs[s] + '/{PLANAR_JSON}'), frames[s]) for s in slabs}}
        """)
    compute = _cell("""
        # Phi = V_vac - E_F, V_vac the maximum of the planar average (the vacuum plateau), as sim-agent
        work_functions = {s: pot.work_function(planar[s], frames[s].attributes.get('Fermi Energy')) for s in slabs}
        profiles = {s: pot.work_function_table(planar[s], work_functions[s], s) for s in slabs}
        work_function = Table('Work functions', {
            'Surface #': np.arange(len(slabs)),
            'Work function (eV)': [work_functions[s]['work_function_eV'] for s in slabs],
            'Vacuum level (eV)': [work_functions[s]['vacuum_level_eV'] for s in slabs],
            'E_F (eV)': [work_functions[s]['fermi_eV'] for s in slabs]},
            labels=list(slabs), attributes={'result': work_functions, 'skipped': skipped})
        {s: round(w['work_function_eV'], 3) for s, w in work_functions.items()}
        """)
    return [Analysis("work_function", title, [fetch, compute], result="work_function")]


#: template -> recipe
RECIPES = {"vasp_band_offset": _band_offset, "vasp_workfunction": _work_function,
           "vasp_polycrystalline_workfunction": _work_function}


def analyses(wf) -> list[Analysis]:
    """The analyses for sim-agent workflow ``wf`` (by its template)."""
    recipe = RECIPES.get(wf.kind)
    return recipe(wf) if recipe else []
