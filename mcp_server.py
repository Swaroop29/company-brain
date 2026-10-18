"""MCP interface for Company Brain's shared FalkorDB incident memory."""
from mcp.server import MCPServer

import tools

mcp = MCPServer("Company Brain")


@mcp.tool()
def analyze_system_impact(system: str) -> dict:
    """Find systems, projects and open tickets affected by a system failure."""
    return tools.impact_analysis(system)["data"]


@mcp.tool()
def start_investigation(system: str) -> dict:
    """Have the Incident Investigator save graph-derived findings as shared memory."""
    return tools.start_incident_investigation(system)["data"]


@mcp.tool()
def read_incident_memory(incident_id: str) -> dict:
    """Read the shared memories, handoffs and follow-up tickets for an incident."""
    return tools.incident_context(incident_id)["data"]


@mcp.tool()
def handoff_to_coordinator(incident_id: str, note: str) -> dict:
    """Persist an Investigator-to-Coordinator handoff for a shared incident."""
    return tools.handoff_incident(incident_id, note)["data"]


@mcp.tool()
def coordinate_followup(incident_id: str) -> dict:
    """Have the Response Coordinator read shared memory and create the recommended ticket."""
    return tools.coordinator_create_followup(incident_id)["data"]
