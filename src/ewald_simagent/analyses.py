"""Analyses of sim-agent workflows, as notebook cells (:class:`ewald.workflow.Analysis`).

A recipe finds the steps it reads, checks that they are there and finished, and writes cells
that load those steps' files and compute the result with EWALD's own kernels
(:mod:`ewald.potential`), in the open: the notebook is the record, and nothing is taken from
sim-agent's ``finalize_workgraph`` output, which the API does not serve.

Steps are found by what they computed when the workflow's archive gave them a ``shape``
(:mod:`.shapes`: a static with a planar average on a slab is a work function's input, whatever
the task is called; the band-offset roles come from the extras sim-agent puts on the input
structures), and by sim-agent's task names otherwise, quoted with the file and constant they
come from. So a new sim-agent template whose statics look like these gets the analysis without
a change here. A workflow whose steps do not match gets the analysis with a ``reason`` instead
of a guess.

To add a template: write ``def _my_template(wf) -> list[Analysis]`` and list it in
:data:`RECIPES`; to offer it to any workflow whose steps fit, list a test in :data:`DETECTORS`.
"""
from __future__ import annotations

import textwrap

from ewald.plugin_api import Analysis

PLANAR_JSON = "locpot_planar_avg.json"

#: vasp_band_offset's statics: frssp/sim_agent src/sim_agent/workgraphs/templates/vasp_band_offset.py
#: (INTERFACE_STATIC, REF_TASKS), the names its finalize reads too.
BAND_OFFSET_STEPS = {"interface": "interface_static", "substrate": "ref_substrate_static",
                     "film": "ref_film_static"}
#: the extras vasp_band_offset puts on its input structures (same file: ROLE_EXTRA, INTERFACE_EXTRA)
ROLE_EXTRA, INTERFACE_EXTRA = "sim_agent_band_offset_role", "sim_agent_interface"


def _cell(text: str) -> str:
    return textwrap.dedent(text).strip()


def _steps(wf) -> dict:
    return {s.name: s for _, s in wf.walk()}


def _shape(step) -> dict:
    return getattr(step, "shape", None) or {}


def _statics(wf, *flags) -> dict:
    """``{name: step}`` of the steps the archive says are statics with all of ``flags``."""
    return {name: st for name, st in _steps(wf).items()
            if _shape(st).get("kind") == "static" and set(flags) <= set(_shape(st).get("flags") or ())}


def _roles_by_extras(wf) -> dict | None:
    """``{role: step name}`` of the lineup's three statics by the extras of their input
    structures (each reference's role, the interface's metadata), None unless exactly one
    potential static has each role."""
    found: dict = {}
    for name, st in _statics(wf, "potential").items():
        extras = getattr(st, "structure_extras", None) or {}
        role = extras.get(ROLE_EXTRA)
        role = role if role in ("substrate", "film") else "interface" if INTERFACE_EXTRA in extras else ""
        if role:
            found.setdefault(role, []).append(name)
    if sorted(found) == sorted(BAND_OFFSET_STEPS) and all(len(v) == 1 for v in found.values()):
        return {role: found[role][0] for role in BAND_OFFSET_STEPS}
    return None


def _band_offset_roles(wf) -> dict:
    """The lineup's statics by their structures' extras, else by vasp_band_offset's task names."""
    return _roles_by_extras(wf) or dict(BAND_OFFSET_STEPS)


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
    roles = _band_offset_roles(wf)
    why = _missing(wf, roles, files=(PLANAR_JSON, "vasprun.xml"))
    if why:
        return [Analysis("lineup", title, reason=why)]
    steps = _steps(wf)
    path = {role: steps[name].path for role, name in roles.items()}
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


def _surface(name: str) -> str:
    """``111_t1`` for ``slab_111_t1_static`` (frssp/sim_agent workgraphs/templates/
    vasp_workfunction.py and vasp_polycrystalline_workfunction.py; their finalize walks the same
    names), else the step's own name."""
    if name.startswith("slab_") and name.endswith("_static") and len(name) > len("slab__static"):
        return name[len("slab_"):-len("_static")]
    return name


def _slab_statics(wf) -> dict:
    """``{surface: step}`` of the slab statics: the steps the archive says are statics of a
    slab, else the ones named ``slab_<hkl>_<term>_static``."""
    shaped = _statics(wf, "slab")
    if shaped:
        return {_surface(name): step for name, step in shaped.items()}
    return {_surface(name): step for name, step in _steps(wf).items()
            if name.startswith("slab_") and name.endswith("_static")}


def _work_function(wf) -> list[Analysis]:
    title = "Work function per surface"
    statics = _slab_statics(wf)
    if not statics:
        return [Analysis("work_function", title, reason="no slab statics (slab_*_static) in this workflow")]
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


def _fits_band_offset(wf) -> bool:
    return _roles_by_extras(wf) is not None


def _fits_work_function(wf) -> bool:
    return any(PLANAR_JSON in (getattr(st, "files", None) or ()) for st in _statics(wf, "slab").values())


#: for a template without a recipe: ``(test, recipe)``, offered when its steps' shapes fit
DETECTORS = ((_fits_band_offset, _band_offset), (_fits_work_function, _work_function))


def analyses(wf) -> list[Analysis]:
    """The analyses for sim-agent workflow ``wf``: its template's recipe, else every recipe
    whose steps the archive's shapes find in it."""
    recipe = RECIPES.get(wf.kind)
    if recipe:
        return recipe(wf)
    return [a for fits, recipe in DETECTORS if fits(wf) for a in recipe(wf)]
