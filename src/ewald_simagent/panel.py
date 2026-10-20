"""The sim-agent side bar of EWALD (a button on the activity bar, under Notebook, Plugins,
Remotes and AI chat): EWALD's workflow side bar over :class:`ewald_simagent.source.SimAgentWorkflows`,
so your recent calculations, their steps, files and structures open with a double click, and a
whole workflow opens as one notebook. Registered through the ``ewald.panels`` entry point."""
from __future__ import annotations

from ewald.plugin_api import workflow_panel

#: a server rack with a pulse, drawn with currentColor so it takes the UI theme's colors
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/>'
        '<path d="M8 7.5h.01M8 16.5h.01M11 7.5h6M11 16.5l1.5-2 1.5 4 1.5-2H17"/></svg>')


sim_agent = workflow_panel("simagent", title="sim-agent", icon=ICON,
                           tooltip="sim-agent: your calculations, their steps and files")
