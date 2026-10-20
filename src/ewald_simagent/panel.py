"""The sim-agent side bar of EWALD (a button on the activity bar, under Notebook, Plugins,
Remotes and AI chat): your recent calculations, their steps, files and structures, opened with a
double click. Registered through the ``ewald.panels`` entry point; the Qt code is imported only
when the side bar first opens (:mod:`ewald_simagent._panel_widget`)."""
from __future__ import annotations

#: a server rack with a pulse, drawn with currentColor so it takes the UI theme's colors
ICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'
        '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/>'
        '<path d="M8 7.5h.01M8 16.5h.01M11 7.5h6M11 16.5l1.5-2 1.5 4 1.5-2H17"/></svg>')


def sim_agent(context):
    """Your sim-agent calculations: recent ones, their steps and files; double-click opens."""
    from ._panel_widget import SimAgentPanel

    return SimAgentPanel(context)


sim_agent.title = "sim-agent"
sim_agent.icon = ICON
