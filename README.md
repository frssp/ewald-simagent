# ewald-simagent

Open your finished sim-agent calculations in EWALD by AiiDA PK, for visualization and
analysis. The plugin only reads: submitting, killing and resubmitting stay in sim-agent.

```bash
pip install "ewald-simagent @ git+https://github.com/frssp/ewald-simagent"
python -m ewald_simagent login http://SERVER/alice/ --user alice     # asks for your sim-agent web password
python -m ewald_simagent login http://SERVER/alice/ --user alice --host supercom=hpc   # SSH alias for a Computer
python -m ewald_simagent show 1234                                   # steps, files, and the path that opens each
```

```python
from ewald import load

pipe = load("simagent:1234")                    # the workflow's first trajectory (its relaxation, restarts joined)
pipe = load("simagent:1234/bands")              # another step, by its name in the workflow
pipe = load("simagent:1234/relax/iteration_02") # one run of a step
pipe = load("simagent:1234/relaxed_structure")  # an output structure
pipe = load("simagent:1234/scf/OUTCAR")         # a retrieved file
pipe = load("simagent:1234/scf/CHGCAR")         # a file left on the cluster, read over SSH
pipe = load("simagent:lab/3f9c2e1a")            # another saved connection; a UUID prefix
```

The same strings work in notebook cells, on the command line (`ewald gui simagent:1234`) and
in saved notebooks, which store only the string.

## How it works

Your sim-agent backend (the address you open in the browser) serves `/api/v1` to your own web
login, and only for your own calculations: another user's PK answers like a PK that does not
exist. For a node it returns the VASP calculations under it in call order, with the files AiiDA
retrieved and the work directory on the cluster. EWALD then reads `vasprun.xml`, `OUTCAR`,
`CONTCAR` and the rest with its own readers. Files that were never retrieved (CHGCAR, LOCPOT)
are read from the work directory through EWALD's SSH remote files, so they need the same SSH
access to the cluster that `load("hpc:/path")` does; a cleaned scratch says so.

Files of a finished calculation are cached under `<ewald config>/cache/simagent/<uuid>/`; a
calculation still running is downloaded again on every load and marked incomplete. The address
and login name are in `<ewald config>/simagent.json`; the password is in the OS keychain
(`ewald.keychain`), never in that file, settings.json or the environment.

Every frame carries `attributes["sim-agent"]` (`pk`, `uuid`, `template`, `calc_pk`, `step`,
`file`, ...) and the numbers of the process's `Dict` outputs (`band_gap`, `total_energy`, ...).

## The contract with sim-agent

The routes and the `sim-agent.node/1` payload are defined in sim-agent
(`src/sim_agent/web/results_api.py`, frssp/sim_agent); sim-agent owns them because it makes
the data. This plugin checks the `schema` field and refuses a payload it does not read.
`tests/conftest.py` is a fake backend that follows that payload; change both together.

## Tests

```bash
pip install -e ".[test]"
pytest
```

The tests run a fake sim-agent backend on localhost; no AiiDA or sim-agent install is needed.
