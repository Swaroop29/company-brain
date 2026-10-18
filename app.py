"""Company Brain: Streamlit chat UI.  Run:  streamlit run app.py"""
import json

import streamlit as st
import streamlit.components.v1 as components

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

# Add each member's exact public LinkedIn URL here when it is available.
TEAM_MEMBERS = [
    {"name": "Swaroop Patil", "linkedin": "https://www.linkedin.com/in/swaroop29/"},
    {"name": "Sarvesh Patil", "linkedin": "https://www.linkedin.com/in/sarveshpatil1007/"},
]


def apply_theme(dark_mode):
    """Apply a simple app-wide light or dark palette without changing the graph evidence canvas."""
    if dark_mode:
        palette = {"app": "#0e1117", "sidebar": "#171b26", "text": "#f3f4f6",
                   "muted": "#aab4c3", "panel": "#202633", "border": "#3a4352"}
    else:
        palette = {"app": "#f8fafc", "sidebar": "#ffffff", "text": "#172033",
                   "muted": "#516174", "panel": "#ffffff", "border": "#d6deea"}
    st.markdown(f"""
    <style>
      .stApp {{ background: {palette['app']}; color: {palette['text']}; }}
      [data-testid="stSidebar"] {{ background: {palette['sidebar']}; border-right: 1px solid {palette['border']}; }}
      [data-testid="stSidebar"] *, .stApp h1, .stApp h2, .stApp h3, .stApp p, .stApp li {{ color: {palette['text']}; }}
      .stApp [data-testid="stCaptionContainer"] *, .stApp small {{ color: {palette['muted']}; }}
      .stApp [data-testid="stExpander"], .stApp [data-testid="stChatMessage"] {{
        background: {palette['panel']}; border-color: {palette['border']}; }}
      .stApp input, .stApp textarea, .stApp [data-baseweb="select"] > div {{
        background: {palette['panel']}; color: {palette['text']}; border-color: {palette['border']}; }}
      /* Team Terminator branding and the compact team menu in the top bar. */
      .st-key-team_brand [data-testid="stMarkdownContainer"] p {{
        font-size: 1.35rem; font-weight: 750; line-height: 1.1; margin: 0; color: {palette['text']}; }}
      .st-key-team_brand [data-testid="stCaptionContainer"] {{ margin-top: 0.15rem; }}
      .st-key-team_menu button {{ font-size: 0.95rem; font-weight: 600; }}
    </style>
    """, unsafe_allow_html=True)


def graph_html(graph, height=430):
    """Render the original physics-based vis-network graph."""
    nodes = [{"id": n["id"], "label": n["text"], "title": n["title"], "shape": "dot", "size": 16,
              "color": COLORS.get(n["label"], "#999"), "font": {"color": "#e6e6e6", "size": 12}}
             for n in graph["nodes"]]
    edges = [{"from": e["from"], "to": e["to"], "label": e["label"], "arrows": "to",
              "font": {"size": 9, "color": "#9aa4b2", "strokeWidth": 0}, "color": {"color": "#5b6573"}}
             for e in graph["edges"]]
    return f"""
    <div id="g" style="height:{height - 10}px;border:1px solid #333;border-radius:8px;background:#0e1117"></div>
    <script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
    <script>
      const network = new vis.Network(document.getElementById('g'),
        {{nodes: new vis.DataSet({json.dumps(nodes)}), edges: new vis.DataSet({json.dumps(edges)})}},
        {{physics: {{stabilization: true, barnesHut: {{springLength: 120}}}}, interaction: {{hover: true}}}});
      let centered = false;
      const centerGraph = function () {{
        network.redraw();
        const positions = Object.values(network.getPositions());
        if (!positions.length) return;
        const minX = Math.min(...positions.map((point) => point.x));
        const maxX = Math.max(...positions.map((point) => point.x));
        const minY = Math.min(...positions.map((point) => point.y));
        const maxY = Math.max(...positions.map((point) => point.y));
        const canvas = document.querySelector('#g canvas');
        if (!canvas.clientWidth || !canvas.clientHeight) return;
        const padding = 96;
        const scale = Math.min(1, canvas.clientWidth / (maxX - minX + padding),
          canvas.clientHeight / (maxY - minY + padding));
        network.moveTo({{
          position: {{x: (minX + maxX) / 2, y: (minY + maxY) / 2}},
          scale: Math.max(scale, 0.25), animation: false
        }});
        centered = true;
      }};
      const resizeObserver = new ResizeObserver(function () {{
        if (!centered) centerGraph();
      }});
      resizeObserver.observe(document.getElementById('g'));
      network.once('stabilizationIterationsDone', centerGraph);
      window.setTimeout(centerGraph, 250);
      window.setTimeout(centerGraph, 900);
    </script>"""

def legend():
    return " ".join(f"<span style='color:{c}'>●</span> {k}" for k, c in COLORS.items())


def graph_records():
    """Read compact node records for the visual explorer from the live FalkorDB graph."""
    records = []
    for (node,) in get_graph().query("MATCH (n) RETURN n").result_set:
        properties = dict(node.properties or {})
        label = node.labels[0] if node.labels else "Node"
        name = properties.get("name") or properties.get("title") or properties.get("text") or properties.get("id") or label
        records.append({"graph_id": node.id, "type": label, "key": properties.get("id") or properties.get("name") or str(node.id),
                        "name": str(name), "properties": properties})
    return sorted(records, key=lambda record: (record["type"], record["name"].lower()))


def graph_neighborhood(graph_id):
    """Read one selected node and its immediate graph evidence for the explorer."""
    ctx = tools.Ctx()
    rows = ctx.run("MATCH (n) WHERE ID(n) = $graph_id RETURN n", graph_id=graph_id)
    if rows:
        ctx.run("MATCH p=(n)-[r]-(m) WHERE ID(n) = $graph_id RETURN p LIMIT 20", graph_id=graph_id)
    return ctx.result({})


SCHEMA_RELATIONSHIPS = {
    "Ownership and dependencies": ("MEMBER_OF · OWNS · DEPENDS_ON", "People belong to teams; teams own systems; systems can depend on other systems.",
                                    "(Person)-[:MEMBER_OF]->(Team)-[:OWNS]->(System)-[:DEPENDS_ON]->(System)"),
    "Projects and knowledge": ("LEADS · USES · AUTHORED · ABOUT", "People lead projects and author documents that describe systems or projects.",
                               "(Person)-[:LEADS]->(Project)-[:USES]->(System)\n(Person)-[:AUTHORED]->(Document)-[:ABOUT]->(System|Project)"),
    "Tickets, messages and decisions": ("ASSIGNED_TO · AFFECTS · POSTED · MENTIONS · SOURCED_FROM", "Operational records connect work, conversations, and decisions to the people and systems involved.",
                                         "(Ticket)-[:ASSIGNED_TO]->(Person)  (Ticket)-[:AFFECTS]->(System)\n(Decision)-[:SOURCED_FROM]->(Document|Ticket|Message)"),
    "Incident memory": ("CONCERNS · HAS_TICKET · WROTE · HANDED_OFF", "Agents store findings and handoffs against an incident, so another role can continue with shared context.",
                        "(Incident)-[:CONCERNS]->(System)  (Incident)-[:HAS_TICKET]->(Ticket)\n(Agent)-[:WROTE]->(Memory)-[:ABOUT]->(Incident)"),
}


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


def open_explore(question=None):
    """Navigate to the chat workspace, optionally carrying a starter question."""
    st.session_state["page_navigation"] = "Explore Knowledge"
    if question:
        st.session_state["pending"] = question


def select_page(page):
    """Select one view from the top navigation bar."""
    st.session_state["page_navigation"] = page


def render_top_navigation():
    """Render the persistent website-style header and return the selected view."""
    pages = ["Home", "Explore Knowledge", "Graph Schema", "About & Demo"]
    with st.container():
        brand, navigation, team = st.columns([2.25, 5.3, 1.0], vertical_alignment="center")
        with brand.container(key="team_brand"):
            st.markdown("**:material/account_tree: Team Terminator**")
            st.caption("Company Brain")
        with navigation.container(horizontal=True, horizontal_alignment="center", wrap=False,
                                  vertical_alignment="center"):
            for item in pages:
                st.button(
                    item,
                    key=f"nav_{item.lower().replace(' ', '_').replace('&', 'and')}",
                    type="primary" if st.session_state["page_navigation"] == item else "tertiary",
                    on_click=select_page,
                    args=(item,),
                )
        with team.container(key="team_menu"):
            with st.popover("Team", icon=":material/groups:", type="tertiary", help="Meet Team Terminator"):
                st.markdown("**Team Terminator**")
                for member in TEAM_MEMBERS:
                    st.markdown(f":material/person: **{member['name']}**")
                    if member["linkedin"]:
                        st.link_button("LinkedIn", member["linkedin"], icon=":material/open_in_new:")
    st.divider()
    return st.session_state["page_navigation"]


st.session_state.setdefault("page_navigation", "Home")
# The app has one intentionally consistent dark appearance.
apply_theme(True)


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
        st.button(q, width="stretch", on_click=open_explore, args=(q,))

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
        st.markdown("**Format example only** — this template is not added to the graph unless you upload a file and select **Import selected records**.")
        st.code('''[
  {"type":"Ticket","id":"T-200","title":"Event bus outage","status":"open",
   "assignee":"Priya Nair","created":"2026-10-17","affects":["event-bus"]}
]''', language="json")
        uploaded = st.file_uploader("JSON file", type="json", key="company_records")
        if uploaded and st.button("Import selected records", width="stretch"):
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

# ------------------------------------------------------------------ pages
page = render_top_navigation()


def render_home():
    st.title("Welcome to Company Brain")
    st.subheader("Find reliable answers from Nimbus Labs' connected company knowledge.")
    st.info("Company Brain links people, teams, systems, projects, documents, tickets, messages, and decisions in FalkorDB.")
    first, second, third = st.columns(3)
    first.metric("Knowledge graph nodes", n)
    second.metric("Connected relationships", r)
    third.metric("Evidence source", "FalkorDB")
    st.markdown("### Start here")
    st.markdown("""
    - **Explore Knowledge** to ask questions such as _Who owns the event bus?_ or _What breaks if it fails?_
    - **Graph Schema** to see the data model and relationships behind each answer.
    - **About & Demo** for the incident, shared-memory, and MCP workflows.
    """)
    st.button("Explore the knowledge graph", type="primary", on_click=open_explore)
    st.caption("Every factual answer is based on a graph query. Open **How I got this** in a response to inspect its Cypher and evidence path.")


def render_schema():
    st.title("Knowledge Graph Explorer")
    st.caption("Browse real FalkorDB records, inspect their properties, and trace the connections around each one.")

    records = graph_records()
    relationship_count = get_graph().query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    types = sorted({record["type"] for record in records})
    summary = st.columns(4)
    summary[0].metric("Graph nodes", len(records))
    summary[1].metric("Relationships", relationship_count)
    summary[2].metric("Node types", len(types))
    summary[3].metric("Data source", "FalkorDB")

    browser, detail = st.columns([1, 1.15], gap="large")
    with browser:
        st.markdown("### Browse nodes")
        selected_type = st.selectbox("Node type", ["All types", *types], key="explorer_type")
        search = st.text_input("Search by name or ID", placeholder="e.g. event-bus, Aisha, T-105", key="explorer_search")
        filtered = [
            record for record in records
            if (selected_type == "All types" or record["type"] == selected_type)
            and search.lower() in f"{record['name']} {record['key']}".lower()
        ]
        if not filtered:
            st.info("No records match those filters.")
            return

        labels = []
        record_by_label = {}
        for record in filtered:
            name = record["name"] if len(record["name"]) <= 58 else f"{record['name'][:55]}..."
            label = f"[{record['type']}] {name} · {record['key']}"
            labels.append(label)
            record_by_label[label] = record
        default = next((index for index, label in enumerate(labels) if "event-bus" in label.lower()), 0)
        selected_label = st.selectbox("Select a record", labels, index=default)
        selected = record_by_label[selected_label]
        st.caption(f"Showing {len(filtered)} of {len(records)} graph records")

    with detail:
        st.markdown("### Selected node")
        st.subheader(selected["name"])
        st.caption(f"{selected['type']} · {selected['key']}")
        property_rows = []
        for key, value in selected["properties"].items():
            display_value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
            property_rows.append({"Property": key.replace("_", " ").title(), "Value": display_value})
        st.dataframe(property_rows or [{"Property": "Graph ID", "Value": selected["graph_id"]}],
                     hide_index=True, use_container_width=True, height=265)

    neighborhood = graph_neighborhood(selected["graph_id"])
    graph = neighborhood["graph"]
    st.markdown("### Relationship map")
    st.caption("A focused view of the selected record and its direct graph connections. Drag nodes, zoom, and hover for details.")
    st.markdown(legend(), unsafe_allow_html=True)
    components.html(graph_html(graph, height=410), height=420)

    st.markdown("### Connected relationships")
    names = {node["id"]: node["text"] for node in graph["nodes"]}
    edge_rows = [
        {"From": names.get(edge["from"], str(edge["from"])), "Relationship": edge["label"],
         "To": names.get(edge["to"], str(edge["to"]))}
        for edge in graph["edges"]
    ]
    if edge_rows:
        st.dataframe(edge_rows, hide_index=True, use_container_width=True)
    else:
        st.info("This record has no direct relationships yet.")

    with st.expander("Schema vocabulary"):
        schema_part = st.selectbox("Choose a relationship group", list(SCHEMA_RELATIONSHIPS), key="schema_relationship")
        relationships, explanation, pattern = SCHEMA_RELATIONSHIPS[schema_part]
        st.markdown(f"**{relationships}**")
        st.write(explanation)
        st.code(pattern, language="text")


def render_about():
    st.title("About Company Brain")
    st.write("Company Brain is a synthetic Nimbus Labs demo for Graph Hacks. FalkorDB is the central store for company knowledge, graph evidence, incident state, agent memory, and follow-up actions.")

    st.markdown("### Three connected tracks")
    with st.container(border=True):
        st.markdown("#### Track 1 · Agents That Act on Connected Data")
        st.write("When a system fails, the agent follows ownership, dependency, project, and ticket relationships in FalkorDB. It identifies the impact and can create a persistent follow-up ticket.")
        st.markdown("**What to demonstrate**")
        st.markdown("1. Open **Respond to a system incident** and choose `event-bus` or `data-warehouse`.  \n2. Review the graph-derived systems, projects, owners, and open tickets.  \n3. Create the follow-up ticket, then run the impact lookup again to show that the new ticket is now part of the graph evidence.")
        st.caption("Why the graph matters: removing FalkorDB removes the dependency paths and the action cannot produce graph-grounded impact or persist its result.")

    with st.container(border=True):
        st.markdown("#### Track 2 · Agent Memory and Coordination")
        st.write("Two roles collaborate through shared FalkorDB state. The Incident Investigator discovers graph impact and saves a report. The Response Coordinator reads the saved incident context and handoff, then decides on the follow-up work.")
        st.markdown("**What to demonstrate**")
        st.markdown("1. Open **Track 2: shared incident memory and handoff** and start an investigation.  \n2. Show the Investigator's saved findings and handoff in **How I got this**.  \n3. Run the Response Coordinator to create a ticket from the saved context, then show the memories, handoff, and ticket connected to the same incident.")
        st.caption("Why the graph matters: the agents share durable incident context through `Incident`, `Memory`, `Handoff`, `Agent`, and `Ticket` relationships.")

    with st.container(border=True):
        st.markdown("#### Track 3 · Company Brain")
        st.write("The app answers questions about Nimbus Labs by joining people, teams, systems, projects, documents, tickets, messages, and decisions. Every answer can show the Cypher and graph evidence behind it.")
        st.markdown("**What to demonstrate**")
        st.markdown("1. Ask a question in **Explore Knowledge**, then open **How I got this**.  \n2. Log a decision or import a JSON record to keep the company knowledge current.  \n3. Open **Graph Schema** and search for the changed record to inspect its live properties and direct connections.")
        st.caption("Why the graph matters: facts are discovered through connected paths, rather than a fixed list of answers or a decorative visualization.")

    st.markdown("### Suggested demo order")
    st.markdown("1. Reset demo data.  \n2. Show Track 3 with a company question and graph evidence.  \n3. Show Track 1 by creating a ticket and proving it changes future results.  \n4. Show Track 2 with Investigator → handoff → Coordinator.  \n5. Finish in Graph Schema by searching for the ticket or incident created during the demo.")
    st.caption("All demo data is synthetic. Use **Reset demo data and workflow records** before recording a clean demo.")


def render_trace(message):
    with st.expander("How I got this: tools, Cypher and graph", expanded=False):
        if message.get("graph", {}).get("nodes"):
            st.markdown(legend(), unsafe_allow_html=True)
            components.html(graph_html(message["graph"]), height=440)
        for step in message.get("trace", []):
            st.markdown(f"**Tool:** `{step['tool']}({json.dumps(step['args'])})`")
            for cypher in step["cypher"]:
                st.code(cypher, language="cypher")


def render_explore():
    st.header("Explore Nimbus Labs knowledge")
    st.caption("Ask a company question in plain language. Each answer uses FalkorDB graph evidence.")
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message["role"] == "assistant":
                render_trace(message)

    question = st.chat_input("e.g. How many active people are in the company?") or st.session_state.pop("pending", None)
    if question:
        with st.chat_message("user"):
            st.markdown(question)
        history = [{"role": message["role"], "content": message["content"]}
                   for message in st.session_state["messages"]]
        st.session_state["messages"].append({"role": "user", "content": question})
        with st.chat_message("assistant"):
            with st.spinner("Walking the graph..."):
                try:
                    out = agent.ask(question, history)
                    reply = {"role": "assistant", "content": out["answer"], "trace": out["trace"],
                             "graph": out["graph"]}
                except Exception as e:
                    reply = {"role": "assistant", "content": f"Something went wrong: `{e}`", "trace": [], "graph": {}}
            st.markdown(reply["content"])
            render_trace(reply)
        st.session_state["messages"].append(reply)


if page == "Home":
    render_home()
elif page == "Graph Schema":
    render_schema()
elif page == "About & Demo":
    render_about()
else:
    render_explore()
