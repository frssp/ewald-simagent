# ewald-simagent

Open your finished sim-agent calculations in EWALD by AiiDA PK, for visualization and
analysis. The plugin only reads: submitting, killing and resubmitting stay in sim-agent.

```bash
pip install "ewald-simagent @ git+https://github.com/frssp/ewald-simagent"
python -m ewald_simagent login http://SERVER/alice/ --user alice     # asks your web password once, keeps a key
python -m ewald_simagent login http://SERVER/alice/ --user alice --key   # or paste a key from the web UI (API keys)
python -m ewald_simagent login http://SERVER/alice/ --user alice --host supercom=hpc   # SSH alias for a Computer
python -m ewald_simagent show 1234                                   # steps, files, and the path that opens each
python -m ewald_simagent doctor                                      # when it does not connect: which step fails, and the fix
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
with your recent calculations, newest first, each with the name of the sim-agent chat that
submitted it dimmed under its title (needs a sim-agent whose API sends `chat`, and an EWALD
with `Workflow.group`). Type a PK or UUID to jump to one, or words (a chat name too) to
search. Expanding a calculation lists its steps (restarts together), their files (CHGCAR and
LOCPOT read from the cluster) and output structures; a double click opens one, and the buttons
insert a `load(...)` cell or copy the path. A double click on the calculation itself (a
WorkGraph) opens it as one notebook: one cell that loads every step (`relax = load(...)`,
`scf = load(...)`) and `pipe` on the main result. The list is sim-agent's `GET /api/v1/nodes`
(newest first, paged with a cursor). The side bar is EWALD's workflow side bar
(`ewald.workflow`); this plugin is its source (`ewald_simagent.source.SimAgentWorkflows`).

1. Plugins side bar › sim-agent results › **Settings** (or Edit › Plugin settings › sim-agent):
   the server address, your login, and either an access key from the web UI (user menu › API
   keys) or your web password, which is used once to make a key and not kept. **Diagnose**
   checks the connection step by step (below), **Log out** forgets it.
2. File › **Open location…** (Ctrl+L) and type `simagent:1234`, or any path above.
3. Or ask in Ask AI ("open the bands of 1234"): EWALD's `list_workflows` and
   `describe_workflow` tools list your calculations and a node's steps, files and structures
   with the path for each, and the AI loads the one you want. `list_workflows` filters by
   formula (any ratio, so `TiN` finds the Hill formula `N4Ti4`), status and day, and
   `open_workflow` opens a step or file by name: "open the TiN calculation that finished
   yesterday" works without a sim-agent specific tool (EWALD with `workflow.find`).

The side bar and the AI tools need an EWALD with workflow sources (`ewald.workflow`), the
settings page and Open location one with plugin settings pages; with an older one
use `python -m ewald_simagent login` and `load()` in a cell.

## When it does not connect

**Diagnose** in the settings (or `python -m ewald_simagent doctor`) checks the saved connection
step by step and stops at the first step that fails, with what to do about it:

| Step | Checks | A failure usually means |
|---|---|---|
| Address | http or https | plain http (sim-agent's IP mode) is a warning: the key is unencrypted |
| Proxy | whether the request goes through a proxy: none when the connection's Proxy is empty, else `system` (`HTTP(S)_PROXY`, OS settings, `NO_PROXY`) or the given one | an office proxy blocks the site server: empty Proxy (settings › Advanced) to connect directly |
| DNS | the server name resolves | wrong name, or not on the site network/VPN |
| Connect | a TCP connection to the server (or the proxy), with its time | firewall, VPN, or nginx down |
| TLS | the https certificate | a self-signed certificate: give its CA file |
| Backend | one request without credentials reaches sim-agent's API, with its time | 504/502 or no answer: the user's backend process is stuck, restart it (`sudo systemctl restart sim-agent-backend@USER.service`); an HTML page: a proxy's block page or the wrong address |
| Access key | `whoami` with the key in this PC's keychain | no key on this PC, or it expired or was revoked: make a new one |
| Account | the key belongs to the login | a key made by another user |

It sends only those two requests, by the same route as every other request of the plugin.

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
never goes to another host; it tells you the address to use instead. The plugin connects to the
server directly and ignores `HTTP_PROXY` and the OS proxy, because a PC's internet proxy often cannot
reach a site server (it answers 504) while the browser goes direct. Set Proxy to `system`
(`--proxy system`) or `http://host:port` when the server is only reachable through one. The address
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

## Analyses

Some templates come with an analysis that the opened notebook ends with, written as ordinary
cells you can read and change (`src/ewald_simagent/analyses.py`, one recipe per template). For
`vasp_band_offset` it is the potential lineup: the cells load `interface_static`,
`ref_substrate_static` and `ref_film_static`, read their retrieved `locpot_planar_avg.json` (no
LOCPOT needed) and `vasprun.xml`, tell substrate from film atoms by plane composition against
the two bulk references, read each side's macroscopic-average window (its primitive d_hkl) from
its layer in the interface, and
move each bulk's VBM and CBM by its plateau shift (V̄ interface plateau minus V̄ bulk); E_F is
the interface's. The last cell leaves `band_offset`, a table of the planar and macroscopic
average with the levels and the band offset or Schottky barriers in its attributes. The numbers
are computed in EWALD (`ewald.potential`), not taken from sim-agent's finalize step, which the
API does not serve; a workflow without one of the three statics or their files gets the reason
instead. In the AI side bar, "draw the potential with VBM, CBM and E_F" runs these cells
(`add_analysis`). Needs an EWALD with `ewald.potential`; an older one opens the workflow without
the analysis.

`vasp_workfunction` and `vasp_polycrystalline_workfunction` end with the work function of every
slab static (`slab_<hkl>_<term>_static`): Phi = V_vac - E_F from its retrieved
`locpot_planar_avg.json` and vasprun.xml, V_vac the maximum of the planar average, as sim-agent
computes it; `work_function` is the table of all surfaces and `profiles` one plot table per
surface on the vacuum level. A slab static without the planar average is named in `skipped`.

### Steps found by what they computed

Before it offers an analysis the plugin fetches the workflow's AiiDA archive without its file
contents (`archive.aiida?content=metadata`, kilobytes, once per finished workflow, kept in the
cache with its files) and opens it with ewald-aiida, which describes every calculation by its
inputs: the INCAR, k-points and input structure with its extras, as a `step.shape` (`kind`:
static, relax, bands, dos, md, phonon; `flags`: slab, potential, cell_relax, charged). The
recipes then find their steps by that rather than by sim-agent's task names: any static of a
slab with a planar average gets its work function, and three potential statics whose structures
carry vasp_band_offset's roles (`sim_agent_band_offset_role`, `sim_agent_interface`, on the
structure or on the relaxation it came from) get the lineup. So a new sim-agent template made of
such steps gets the analysis without a new release of this plugin; a template with a recipe of
its own still uses that one. A running workflow (AiiDA exports only finished ones), a sim-agent
without the archive route or a failing archive leave the steps as they were, and the recipes go
by the task names as before; `wf.shapes_from` says why ("" when the archive was read).

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

The tests run a fake sim-agent backend on localhost; no AiiDA profile or sim-agent install is
needed (the fake serves its archives as JSON; opening real ones is ewald-aiida's to test).

## License

MIT, like the rest of the EWALD plugins ([LICENSE](LICENSE)).
