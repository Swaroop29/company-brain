"""Company Brain: Streamlit chat UI.  Run:  streamlit run app.py"""
import json

import streamlit as st

import agent
import tools
from db import GRAPH_NAME, get_graph, graph_is_empty
from ingest import ingest

st.set_page_config(page_title="Company Brain", page_icon="🧠", layout="wide")

COLORS = {"Person": "#4C9AFF", "Team": "#79E2F2", "System": "#36B37E", "Project": "#FFAB00",
          "Document": "#B3A1FF", "Ticket": "#FF8F73", "Message": "#C1C7D0", "Decision": "#FF5630"}

EXAMPLES = [
    "Why did we move the event bus from RabbitMQ to Kafka?",
    "Who knows how to rotate the auth signing keys, and are they still here?",
    "What breaks if the event bus goes down?",
    "Which systems are at risk because people left?",
    "How is Dana Whitfield connected to payments-service?",
    "Which systems are the most critical?",
]


_GRAPH_HTML = """
<div class="graph-shell">
  <div class="graph-help">Drag nodes or the background · use the mouse wheel to zoom</div>
  <button class="reset-view" type="button">Reset view</button>
  <svg class="evidence-graph" viewBox="0 0 1000 520" aria-label="Interactive graph evidence"></svg>
</div>
"""

_GRAPH_CSS = """
.graph-shell { position: relative; height: 440px; border: 1px solid var(--st-border-color, #3b4252);
  border-radius: 10px; background: var(--st-secondary-background-color, #151923); overflow: hidden; }
.evidence-graph { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
.evidence-graph:active { cursor: grabbing; }
.graph-help { position: absolute; z-index: 1; left: 12px; top: 10px; font: 12px sans-serif;
  color: var(--st-text-color, #dce3ec); opacity: .75; pointer-events: none; }
.reset-view { position: absolute; z-index: 1; right: 12px; top: 8px; border: 1px solid var(--st-border-color, #64748b);
  border-radius: 6px; padding: 4px 8px; cursor: pointer; color: var(--st-text-color, #dce3ec);
  background: var(--st-secondary-background-color, #1f2937); }
"""

_GRAPH_JS = """
export default function (component) {
  const { data, parentElement } = component
  const svg = parentElement.querySelector(".evidence-graph")
  const reset = parentElement.querySelector(".reset-view")
  if (!svg || !data) return

  const ns = "http://www.w3.org/2000/svg"
  const make = (tag, attrs = {}) => {
    const el = document.createElementNS(ns, tag)
    Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, value))
    return el
  }
  const state = parentElement.__graphState || {
    positions: new Map(), viewport: { x: 0, y: 0, scale: 1 }, drag: null,
  }
  parentElement.__graphState = state
  const nodes = data.nodes || []
  const edges = data.edges || []
  const ids = new Set(nodes.map((node) => String(node.id)))
  for (const id of state.positions.keys()) if (!ids.has(id)) state.positions.delete(id)
  const columns = Math.max(1, Math.ceil(Math.sqrt(nodes.length)))
  nodes.forEach((node, index) => {
    const id = String(node.id)
    if (!state.positions.has(id)) {
      const column = index % columns
      const row = Math.floor(index / columns)
      const rows = Math.max(1, Math.ceil(nodes.length / columns))
      state.positions.set(id, {
        x: 120 + (columns === 1 ? 0 : column * 760 / (columns - 1)),
        y: 145 + (rows === 1 ? 0 : row * 260 / (rows - 1)),
      })
    }
  })

  svg.replaceChildren()
  const defs = make("defs")
  const marker = make("marker", { id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5",
    markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" })
  marker.appendChild(make("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#718096" }))
  defs.appendChild(marker)
  svg.appendChild(defs)
  const viewport = make("g")
  svg.appendChild(viewport)
  const edgeLayer = make("g")
  const nodeLayer = make("g")
  viewport.append(edgeLayer, nodeLayer)

  const edgeElements = []
  edges.forEach((edge) => {
    const group = make("g")
    const line = make("line", { stroke: "#718096", "stroke-width": "1.6", "marker-end": "url(#arrow)" })
    const label = make("text", { fill: "#9aa4b2", "font-size": "11", "text-anchor": "middle" })
    label.textContent = edge.label
    group.append(line, label)
    edgeLayer.appendChild(group)
    edgeElements.push({ edge, line, label })
  })

  const nodeElements = new Map()
  nodes.forEach((node) => {
    const group = make("g", { "data-node-id": String(node.id), cursor: "grab" })
    const circle = make("circle", { r: "31", fill: node.color || "#64748b", stroke: "#e2e8f0", "stroke-width": "1.5" })
    const text = make("text", { fill: "#0e1117", "font-size": "12", "font-weight": "700", "text-anchor": "middle", y: "4" })
    text.textContent = String(node.text).slice(0, 18)
    const title = make("title")
    title.textContent = `${node.text} (${node.label})\\n${node.title || ""}`
    group.append(circle, text, title)
    nodeLayer.appendChild(group)
    nodeElements.set(String(node.id), group)
  })

  const update = () => {
    viewport.setAttribute("transform", `translate(${state.viewport.x} ${state.viewport.y}) scale(${state.viewport.scale})`)
    nodeElements.forEach((group, id) => {
      const point = state.positions.get(id)
      group.setAttribute("transform", `translate(${point.x} ${point.y})`)
    })
    edgeElements.forEach(({ edge, line, label }) => {
      const from = state.positions.get(String(edge.from))
      const to = state.positions.get(String(edge.to))
      if (!from || !to) return
      line.setAttribute("x1", from.x); line.setAttribute("y1", from.y)
      line.setAttribute("x2", to.x); line.setAttribute("y2", to.y)
      label.setAttribute("x", (from.x + to.x) / 2)
      label.setAttribute("y", (from.y + to.y) / 2 - 7)
    })
  }
  const pointFor = (event) => {
    const box = svg.getBoundingClientRect()
    return { x: (event.clientX - box.left) * 1000 / box.width,
      y: (event.clientY - box.top) * 520 / box.height }
  }
  svg.onpointerdown = (event) => {
    const target = event.target.closest("[data-node-id]")
    const point = pointFor(event)
    state.drag = target ? { type: "node", id: target.dataset.nodeId, point } : { type: "pan", point }
    svg.setPointerCapture(event.pointerId)
  }
  svg.onpointermove = (event) => {
    if (!state.drag) return
    const point = pointFor(event)
    if (state.drag.type === "node") {
      state.positions.set(state.drag.id, { x: (point.x - state.viewport.x) / state.viewport.scale,
        y: (point.y - state.viewport.y) / state.viewport.scale })
    } else {
      state.viewport.x += point.x - state.drag.point.x
      state.viewport.y += point.y - state.drag.point.y
      state.drag.point = point
    }
    update()
  }
  svg.onpointerup = () => { state.drag = null }
  svg.onpointercancel = () => { state.drag = null }
  svg.onwheel = (event) => {
    event.preventDefault()
    const factor = event.deltaY < 0 ? 1.12 : 0.89
    state.viewport.scale = Math.max(0.45, Math.min(2.6, state.viewport.scale * factor))
    update()
  }
  reset.onclick = () => { state.viewport = { x: 0, y: 0, scale: 1 }; update() }
  update()
}
"""

_INTERACTIVE_GRAPH = st.components.v2.component(
    "interactive_evidence_graph", html=_GRAPH_HTML, css=_GRAPH_CSS, js=_GRAPH_JS,
)


def interactive_graph(graph, key):
    """Render a draggable, zoomable graph without using deprecated component APIs."""
    nodes = [{**node, "color": COLORS.get(node["label"], "#64748b")} for node in graph["nodes"]]
    _INTERACTIVE_GRAPH(data={"nodes": nodes, "edges": graph["edges"]}, key=key, width="stretch", height=440)


def legend():
    return " ".join(f"<span style='color:{c}'>●</span> {k}" for k, c in COLORS.items())


def add_incident_message(heading, result):
    """Add a persisted multi-agent workflow update to the main chat."""
    data = result["data"]
    content = (
        f"### {heading}: `{data['id']}`\n\n"
        f"**System:** {data['system']} · **Status:** {data['status']}\n\n"
        f"**Shared memories:** {len(data['memories'])} · **Handoffs:** {len(data['handoffs'])} · "
        f"**Follow-up tickets:** {len(data['tickets'])}\n\n"
        "Open **How I got this** to see the shared incident graph and its handoff path."
    )
    st.session_state.setdefault("messages", []).append({
        "role": "assistant", "content": content,
        "trace": [{"tool": "incident_context", "args": {"incident_id": data["id"]},
                   "cypher": result["cypher"]}],
        "graph": result["graph"]})


def add_agent_message(run):
    """Show one role's independent report and the incident state it saved to the shared graph."""
    result = run["result"]
    data = result["data"]
    mode = "LLM report from verified graph evidence" if run["used_llm"] else "Graph-derived fallback report"
    content = (
        f"### {run['role']} completed: `{data['id']}`\n\n"
        f"**Report:** {run['summary']}\n\n"
        f"**Context used:** {'system impact evidence' if run['role'] == 'Incident Investigator' else 'saved incident memory, handoff, and current graph ownership data'}\n\n"
        f"**Saved to shared memory:** {len(data['memories'])} finding(s) · "
        f"**Handoffs:** {len(data['handoffs'])} · **Follow-up tickets:** {len(data['tickets'])}\n\n"
        f"_{mode}. Open **How I got this** to inspect the persisted graph context._"
    )
    st.session_state.setdefault("messages", []).append({
        "role": "assistant", "content": content,
        "trace": [{"tool": "incident_context", "args": {"incident_id": data["id"]},
                   "cypher": result["cypher"]}],
        "graph": result["graph"]})


@st.cache_resource
def boot():
    """Connect and auto-load the demo data the first time."""
    if graph_is_empty():
        ingest()
    return True


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🧠 Company Brain")
    st.caption("Nimbus Labs knowledge graph on FalkorDB")
    try:
        boot()
        g = get_graph()
        n = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
        r = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        st.success(f"Graph '{GRAPH_NAME}': {n} nodes, {r} relationships")
    except Exception as e:
        st.error(f"Cannot reach FalkorDB: {e}")
        st.stop()

    st.markdown("**Try a question**")
    for q in EXAMPLES:
        if st.button(q, width="stretch"):
            st.session_state["pending"] = q

    with st.expander("Log a new decision (keeps the graph current)"):
        people = [r[0] for r in g.query("MATCH (p:Person {status:'active'}) RETURN p.name ORDER BY p.name").result_set]
        systems = [r[0] for r in g.query("MATCH (s) WHERE s:System OR s:Project RETURN s.name ORDER BY s.name").result_set]
        decs = [""] + [f"{r[0]}: {r[1]}" for r in g.query("MATCH (d:Decision) RETURN d.id, d.title ORDER BY d.id").result_set]
        with st.form("add_decision", clear_on_submit=True):
            title = st.text_input("Decision")
            why = st.text_area("Why")
            by = st.selectbox("Decided by", people)
            about = st.multiselect("About", systems)
            replaces = st.selectbox("Replaces an earlier decision?", decs)
            if st.form_submit_button("Add to graph") and title:
                new_id = tools.add_decision(title, why, by, about, replaces.split(":")[0] if replaces else None)
                st.success(f"Added {new_id}. Ask the agent about it.")

    with st.expander("Import or update company records"):
        st.caption("Upload a JSON list of Documents, Tickets, or Messages. An existing id is updated in place.")
        st.code('''[
  {"type":"Ticket","id":"T-200","title":"Event bus outage","status":"open",
   "assignee":"Priya Nair","created":"2026-10-17","affects":["event-bus"]}
]''', language="json")
        uploaded = st.file_uploader("JSON file", type="json", key="company_records")
        if uploaded and st.button("Import records", width="stretch"):
            try:
                payload = json.loads(uploaded.getvalue().decode("utf-8"))
                records = payload.get("records") if isinstance(payload, dict) else payload
                imported = tools.import_records(records)
                st.success(f"Imported or updated {len(imported)} record(s): " + ", ".join(r["id"] for r in imported))
                st.rerun()
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as e:
                st.error(f"Could not import records: {e}")
            except Exception as e:
                st.error(f"Import failed: {e}")

    with st.expander("Respond to a system incident"):
        st.caption("The graph suggests an owner and priority. Review the plan, then create the follow-up ticket.")
        incident_systems = [row[0] for row in g.query("MATCH (s:System) RETURN s.name ORDER BY s.name").result_set]
        incident_system = st.selectbox("Affected system", incident_systems, key="incident_system")
        response = tools.incident_response(incident_system)["data"]
        action = response["recommended_action"]
        assignees = [owner["name"] for owner in response["available_assignees"]]
        st.write(f"**Graph finding:** {action['reason']}")
        st.write("**Suggested owner:** " + (action["suggested_assignee"] or "No active owner found"))
        if response["affected_systems"]:
            st.write("**Affected systems:** " + ", ".join(response["affected_systems"]))
        if assignees:
            with st.form("create_incident_followup", clear_on_submit=True):
                ticket_title = st.text_input("Follow-up ticket", value=action["title"])
                assignee = st.selectbox("Assign to", assignees)
                priorities = ["medium", "high", "critical"]
                priority = st.selectbox("Priority", priorities, index=priorities.index(action["priority"]))
                if st.form_submit_button("Create follow-up ticket") and ticket_title:
                    try:
                        impact_before = tools.impact_analysis(incident_system)
                        ticket = tools.create_incident_followup(incident_system, ticket_title, assignee, priority)
                        ticket_view = tools.get_item(ticket["id"])
                        impact_after = tools.impact_analysis(ticket["system"])
                        before_count = len(impact_before["data"]["open_tickets_on_this_system"])
                        after_tickets = impact_after["data"]["open_tickets_on_this_system"]
                        after_count = len(after_tickets)
                        appears_after = any(item["id"] == ticket["id"] for item in after_tickets)
                        content = (
                            f"### Follow-up ticket created: `{ticket['id']}`\n\n"
                            f"**{ticket_title}** is now an open `{priority}` priority ticket assigned to "
                            f"{assignee}.\n\n"
                            "The graph now includes:\n\n"
                            f"- `{ticket['id']}` → `ASSIGNED_TO` → {assignee}\n"
                            f"- `{ticket['id']}` → `AFFECTS` → {ticket['system']}\n\n"
                            f"**Before → after proof:** impact analysis had `{before_count}` open ticket(s) before this "
                            f"action and `{after_count}` afterward. `{ticket['id']}` is "
                            f"{'now included' if appears_after else 'not yet included'} in later impact analysis results.\n\n"
                            "Open **How I got this** to see the graph path and Cypher used to read the new ticket."
                        )
                        st.session_state.setdefault("messages", []).append({
                            "role": "assistant", "content": content,
                            "trace": [
                                {"tool": "impact_analysis (before)", "args": {"system": ticket["system"]},
                                 "cypher": impact_before["cypher"]},
                                {"tool": "get_item", "args": {"item_id": ticket["id"]},
                                 "cypher": ticket_view["cypher"]},
                                {"tool": "impact_analysis (after)", "args": {"system": ticket["system"]},
                                 "cypher": impact_after["cypher"]},
                            ],
                            "graph": impact_after["graph"]})
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not create follow-up ticket: {e}")
        else:
            st.warning("No active owner is available for this system.")

    with st.expander("Track 2: shared incident memory and handoff"):
        st.caption("Two separate role prompts work from graph evidence. Their reports, handoff and ticket persist in FalkorDB.")
        coordination_systems = [row[0] for row in g.query("MATCH (s:System) RETURN s.name ORDER BY s.name").result_set]
        coordination_system = st.selectbox("System to investigate", coordination_systems, key="coordination_system")
        if st.button("Run Incident Investigator", width="stretch"):
            try:
                run = agent.investigate_incident(coordination_system)
                add_agent_message(run)
                st.rerun()
            except Exception as e:
                st.error(f"Could not start investigation: {e}")

        incidents = tools.list_incidents()
        if incidents:
            choices = {f"{item['id']} · {item['system']} · {item['status']}": item["id"] for item in incidents}
            selected_label = st.selectbox("Continue a shared incident", list(choices), key="shared_incident")
            incident_id = choices[selected_label]
            current = tools.incident_context(incident_id)["data"]
            st.write(f"**Saved memory:** {len(current['memories'])} finding(s) · "
                     f"**handoffs:** {len(current['handoffs'])} · "
                     f"**tickets:** {len(current['tickets'])}")
            with st.form("handoff_incident"):
                handoff_note = st.text_area("Handoff note", value="Investigation complete; please coordinate recovery.")
                if st.form_submit_button("Hand off to Response Coordinator"):
                    try:
                        result = tools.handoff_incident(incident_id, handoff_note)
                        add_incident_message("Investigator handed off the incident", result)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not hand off incident: {e}")
            if not current["tickets"]:
                if st.button("Run Response Coordinator", width="stretch"):
                    try:
                        run = agent.coordinate_incident(incident_id)
                        add_agent_message(run)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not create coordinated follow-up: {e}")

    st.caption("Reset removes imported records, generated tickets, incidents, memories and handoffs.")
    if st.button("Reset demo data and workflow records", width="stretch"):
        ingest()
        st.session_state.pop("messages", None)
        st.rerun()

# ------------------------------------------------------------------ chat
st.header("Ask anything about how Nimbus Labs works")
st.caption("Every answer comes from graph queries. Open 'How I got this' to see the Cypher and the path.")

if "messages" not in st.session_state:
    st.session_state["messages"] = []


def render_trace(m, graph_key):
    with st.expander("How I got this: tools, Cypher and graph", expanded=False):
        if m.get("graph", {}).get("nodes"):
            st.markdown(legend(), unsafe_allow_html=True)
            interactive_graph(m["graph"], graph_key)
        for step in m.get("trace", []):
            st.markdown(f"**Tool:** `{step['tool']}({json.dumps(step['args'])})`")
            for c in step["cypher"]:
                st.code(c, language="cypher")


for message_index, m in enumerate(st.session_state["messages"]):
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_trace(m, f"evidence-graph-{message_index}")

question = st.chat_input("e.g. Why did we choose Kafka?") or st.session_state.pop("pending", None)
if question:
    with st.chat_message("user"):
        st.markdown(question)
    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state["messages"]]
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("assistant"):
        with st.spinner("Walking the graph..."):
            try:
                out = agent.ask(question, history)
                msg = {"role": "assistant", "content": out["answer"], "trace": out["trace"], "graph": out["graph"]}
            except Exception as e:
                msg = {"role": "assistant", "content": f"Something went wrong: `{e}`", "trace": [], "graph": {}}
        st.markdown(msg["content"])
        render_trace(msg, "evidence-graph-new")
    st.session_state["messages"].append(msg)
