# ewald-simagent

Open your finished sim-agent calculations in EWALD by AiiDA PK, for visualization and
analysis. The plugin only reads: submitting, killing and resubmitting stay in sim-agent.

```bash
pip install "ewald-simagent @ git+https://github.com/frssp/ewald-simagent"
python -m ewald_simagent login http://SERVER/alice/ --user alice     # asks your web password once, keeps a key
python -m ewald_simagent login http://SERVER/alice/ --user alice --key   # or paste a key from the web UI (API keys)
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
pipe = load("simagent:lab/3f9c2e1a")            # another saved connection; a UUID or its beginning
```

The same strings work in notebook cells, on the command line (`ewald gui simagent:1234`) and
in saved notebooks, which store only the string.

## In the GUI, without Python

The **sim-agent** button on the activity bar (the icon column at the left) opens a side bar
with your recent calculations, newest first. Type a PK or UUID to jump to one, or words to
search. Expanding a calculation lists its steps (restarts together), their files (CHGCAR and
LOCPOT read from the cluster) and output structures; a double click opens one, and the buttons
insert a `load(...)` cell or copy the path. The list is sim-agent's `GET /api/v1/nodes` (newest first, paged with a cursor).

1. Plugins side bar › sim-agent results › **Settings** (or Edit › Plugin settings › sim-agent):
   the server address, your login, and either an access key from the web UI (user menu › API
   keys) or your web password, which is used once to make a key and not kept. **Test** checks
   the login, **Log out** forgets it.
2. File › **Open location…** (Ctrl+L) and type `simagent:1234`, or any path above.
3. Or ask in Ask AI ("open the bands of 1234"): the `simagent_node` tool lists a node's steps,
   files and structures with the path for each, and the AI loads the one you want.

The settings page and Open location need an EWALD with plugin settings pages; with an older one
use `python -m ewald_simagent login` and `load()` in a cell.

## How it works

Your sim-agent backend (the address you open in the browser) serves a read-only results API
under `/api/v1`, and only for your own calculations: another user's PK answers like a PK that
does not exist. Data requests take an access key only. `login` either asks your web
password once and makes a key with it (`POST /api/v1/tokens`, 180 days), or with `--key`
takes a key you made in the web UI (user menu > API keys); either way only the key is kept.
Revoke it under API keys when a machine should lose access.

For a node it lists the calculations under it in call order, with their retrieved files and
work directories on the cluster. Files
AiiDA retrieved (`vasprun.xml`, `OUTCAR`, `CONTCAR`) are downloaded from sim-agent. Files that
were never retrieved (CHGCAR, LOCPOT) are read from the calculation's work directory through
EWALD's SSH remote files, so they need the same SSH access to the cluster that
`load("hpc:/path")` does; once scratch is cleaned only the retrieved files are left. EWALD
reads all of them with its own readers.

Files of a finished calculation are cached under `<ewald config>/cache/simagent/<uuid>/`, up to
`cache_limit_gb` in `simagent.json` (10 GB by default; the least recently opened go first); a
calculation still running is downloaded again on every load and marked incomplete.

Use the server's `https://` address when it has one. A plain `http://` address to another
machine sends the key unencrypted, so `login` asks before saving it (`--allow-http` skips the
question; the settings page has Allow plain http). For a self-signed certificate give its CA
bundle (`--ca-file`, or CA file in the settings). The plugin does not follow redirects, so a key
never goes to another host; it tells you the address to use instead. On a PC with `HTTP_PROXY`
set, add the server to `NO_PROXY`, or requests (and the key) go through the proxy. The address
and login name are in `<ewald config>/simagent.json`; the access key is in the OS keychain
(`ewald.keychain`), never in that file, settings.json or the environment.

Every frame carries `attributes["sim-agent"]` (`pk`, `uuid`, `template`, `calc_pk`, `step`,
`file`, ...) and, under `scalars`, the numbers of the process's `Dict` outputs by output name
(`{"misc": {"band_gap": ..., "total_energy": ...}}`, VASP units). They describe the whole
process, so they are not frame attributes of their own.

A step's trajectory joins only the restarts of one calculation (call paths that differ in a
last `iteration_NN`). A step that holds separate calculations, such as one DFT run per
structure in an MLFF workflow, opens its first one and warns with the paths of the others.

`show` and the AI tool give paths as `simagent:CONNECTION/UUID-PREFIX`: a PK is unique only in
one server's database, so that is the form to keep in a notebook.

## The contract with sim-agent

The routes and the payload are sim-agent's results API v1 (`sim-agent.node/1`), defined in
frssp/sim_agent `docs/api/results-api-v1.md`; sim-agent owns it because it makes the data.
This plugin checks the `schema` field and refuses a version it does not read.
`tests/conftest.py` is a fake backend that follows that document; change both together.

## Tests

```bash
pip install -e ".[test]"
pytest
```

The tests run a fake sim-agent backend on localhost; no AiiDA or sim-agent install is needed.
