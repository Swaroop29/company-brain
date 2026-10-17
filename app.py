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


def graph_html(graph, height=430):
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
      new vis.Network(document.getElementById('g'),
        {{nodes: new vis.DataSet({json.dumps(nodes)}), edges: new vis.DataSet({json.dumps(edges)})}},
        {{physics: {{stabilization: true, barnesHut: {{springLength: 120}}}}, interaction: {{hover: true}}}});
    </script>"""


def legend():
    return " ".join(f"<span style='color:{c}'>●</span> {k}" for k, c in COLORS.items())


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
        if st.button(q, use_container_width=True):
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
        if uploaded and st.button("Import records", use_container_width=True):
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

    if st.button("Reset demo data"):
        ingest()
        st.session_state.pop("messages", None)
        st.rerun()

# ------------------------------------------------------------------ chat
st.header("Ask anything about how Nimbus Labs works")
st.caption("Every answer comes from graph queries. Open 'How I got this' to see the Cypher and the path.")

if "messages" not in st.session_state:
    st.session_state["messages"] = []


def render_trace(m):
    with st.expander("How I got this: tools, Cypher and graph", expanded=False):
        if m.get("graph", {}).get("nodes"):
            st.markdown(legend(), unsafe_allow_html=True)
            components.html(graph_html(m["graph"]), height=440)
        for step in m.get("trace", []):
            st.markdown(f"**Tool:** `{step['tool']}({json.dumps(step['args'])})`")
            for c in step["cypher"]:
                st.code(c, language="cypher")


for m in st.session_state["messages"]:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] == "assistant":
            render_trace(m)

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
        render_trace(msg)
    st.session_state["messages"].append(msg)
