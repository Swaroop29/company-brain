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


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🧠 Company Brain")
    st.caption("Nimbus Labs knowledge graph on FalkorDB")
    page = st.radio("Navigate", ["Home", "Explore Knowledge", "Graph Schema", "About & Demo"],
                    key="page_navigation")
    if "dark_mode" not in st.session_state:
        st.session_state["dark_mode"] = True
    st.toggle("Dark mode", key="dark_mode")
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
apply_theme(st.session_state["dark_mode"])


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
    st.title("Graph Schema")
    st.caption("This is the connected data model Company Brain queries in FalkorDB.")
    st.code("""(Person)-[:MEMBER_OF]->(Team)-[:OWNS]->(System)-[:DEPENDS_ON]->(System)
(Person)-[:LEADS]->(Project)-[:USES]->(System)
(Person)-[:AUTHORED]->(Document)-[:ABOUT]->(System|Project)
(Ticket)-[:ASSIGNED_TO]->(Person)      (Ticket)-[:AFFECTS]->(System)
(Person)-[:POSTED]->(Message)-[:MENTIONS]->(System|Project)
(Decision)-[:DECIDED_BY]->(Person)     (Decision)-[:CONCERNS]->(System|Project)
(Decision)-[:SOURCED_FROM]->(Document|Ticket|Message)
(Incident)-[:CONCERNS]->(System)       (Incident)-[:HAS_TICKET]->(Ticket)
(Agent)-[:WROTE]->(Memory)-[:ABOUT]->(Incident)
(Agent)-[:HANDED_OFF]->(Handoff)-[:FOR_INCIDENT]->(Incident)""", language="text")
    st.markdown("### How the graph helps")
    st.markdown("""
    - **Impact analysis** follows dependency edges to find what a failed system affects.
    - **Knowledge lookup** connects people with the documents, tickets, decisions, and messages they contributed.
    - **Incident coordination** persists reports, handoffs, and tickets as shared graph memory.
    - **Open questions** use a validated read-only Cypher fallback, with the generated query shown in the trace.
    """)


def render_about():
    st.title("About Company Brain")
    st.write("Company Brain is a synthetic Nimbus Labs demo built for Graph Hacks using FalkorDB as its central data store.")
    st.markdown("### What you can do")
    st.markdown("""
    1. **Ask company questions** and inspect the graph evidence behind each answer.
    2. **Keep knowledge current** by logging a decision or importing documents, tickets, and messages.
    3. **Respond to an incident** with graph-derived impact, ownership, and a persistent follow-up ticket.
    4. **Coordinate an incident** as two roles that store reports and handoffs in shared FalkorDB memory.
    """)
    st.markdown("### Demo path")
    st.markdown("Ask _What breaks if the event bus goes down?_, create the proposed follow-up ticket, then open **How I got this** to see the graph change. Next, run the Investigator and Response Coordinator from the sidebar.")
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
