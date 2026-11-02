"""The sim-agent side bar of EWALD (a button on the activity bar, under Notebook, Plugins,
Remotes and AI chat): EWALD's workflow side bar over :class:`ewald_simagent.source.SimAgentWorkflows`,
so your recent calculations, their steps, files and structures open with a double click, and a
whole workflow opens as one notebook. Registered through the ``ewald.panels`` entry point."""
from __future__ import annotations

from ewald.plugin_api import workflow_panel

#: a camera lens like sim-agent's own logo (ring, lens, glint, center), drawn with currentColor
#: so it takes the UI theme's colors
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<circle cx="12" cy="12" r="9.2"/><circle cx="12" cy="12" r="5.4"/>'
        '<path d="M9.06 10.30A3.4 3.4 0 0 1 14.60 9.81" stroke-width="1.3"/>'
        '<circle cx="12" cy="12" r="1.1" fill="currentColor" stroke="none"/></svg>')


sim_agent = workflow_panel("simagent", title="sim-agent", icon=ICON,
                           tooltip="sim-agent: your calculations, their steps and files")
