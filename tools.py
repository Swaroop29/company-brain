"""Graph tools the agent can call. Each one runs Cypher against FalkorDB.

Every tool returns a dict:
  data    -> what the LLM reads (JSON-serialisable)
  graph   -> {"nodes": [...], "edges": [...]} subgraph used to draw the answer path
  cypher  -> the Cypher queries that ran (shown in the UI as the "how I got this" trace)
"""
from collections import defaultdict

from falkordb import Edge, Node, Path

from db import get_graph


# ---------------------------------------------------------------- helpers
class Ctx:
    """Collects the queries run and the subgraph touched by one tool call."""

    def __init__(self):
        self.cypher = []
        self.nodes = {}
        self.edges = {}

    def run(self, cypher, **params):
        res = get_graph().query(cypher, params)
        shown = " ".join(cypher.split())
        if params:
            short = {k: (v if not isinstance(v, list) or len(v) < 6 else f"[{len(v)} items]")
                     for k, v in params.items()}
            shown += f"   // params: {short}"
        self.cypher.append(shown)
        for row in res.result_set:
            for value in row:
                self._collect(value)
        return res.result_set

    def _collect(self, v):
        if isinstance(v, Node):
            props = v.properties or {}
            label = v.labels[0] if v.labels else "Node"
            text = props.get("name") or props.get("title") or props.get("text") or props.get("id") or label
            if label in ("Document", "Ticket", "Decision", "Message") and props.get("id"):
                text = props["id"]
            self.nodes[v.id] = {"id": v.id, "label": label, "text": str(text)[:40],
                                "title": str(props.get("title") or props.get("text") or props.get("name")
                                             or props.get("id") or "")[:300]}
        elif isinstance(v, Edge):
            self.edges[(v.src_node, v.dest_node, v.relation)] = {
                "from": v.src_node, "to": v.dest_node, "label": v.relation}
        elif isinstance(v, Path):
            for n in v.nodes():
                self._collect(n)
            for e in v.edges():
                self._collect(e)
        elif isinstance(v, list):
            for x in v:
                self._collect(x)

    def result(self, data):
        return {"data": data,
                "graph": {"nodes": list(self.nodes.values()), "edges": list(self.edges.values())},
                "cypher": self.cypher}


def _variants(text):
    k = text.lower().strip()
    return list({k, k.replace(" ", "-"), k.replace("-", " ")})


def _resolve(ctx, text, labels=("System", "Project")):
    """Find the graph entity a user's words refer to. Returns (label, name) or None."""
    ks = _variants(text)
    best = None
    for label in labels:
        rows = ctx.run(
            f"MATCH (n:{label}) WHERE toLower(n.name) IN $ks OR "
            f"toLower(n.name) CONTAINS $k0 OR toLower(n.name) CONTAINS $k1 "
            f"RETURN n.name ORDER BY size(n.name)", ks=ks, k0=ks[0], k1=ks[-1])
        if rows:
            exact = [r[0] for r in rows if r[0].lower() in ks]
            cand = (exact or [rows[0][0]])[0]
            if best is None or r_is_exact(cand, ks):
                best = (label, cand)
                if r_is_exact(cand, ks):
                    break
    return best


def r_is_exact(name, ks):
    return name.lower() in ks


def _not_found(text, labels="System or Project"):
    return {"error": f"No {labels} matching '{text}'. Try the search tool with a keyword."}


# ---------------------------------------------------------------- tools
def search(keyword: str):
    """Keyword search across names, titles and message text."""
    ctx = Ctx()
    k = keyword.lower().strip()
    rows = ctx.run(
        "MATCH (n) WHERE toLower(coalesce(n.name,'')) CONTAINS $k OR toLower(coalesce(n.title,'')) CONTAINS $k "
        "OR toLower(coalesce(n.text,'')) CONTAINS $k OR toLower(coalesce(n.rationale,'')) CONTAINS $k "
        "OR toLower(coalesce(n.summary,'')) CONTAINS $k "
        "RETURN labels(n)[0], coalesce(n.id, n.name), coalesce(n.name, n.title, n.text) LIMIT 15", k=k)
    return ctx.result([{"type": r[0], "id_or_name": r[1], "label": str(r[2])[:120]} for r in rows])


def get_item(item_id: str):
    """Read the full text of a source item (document D3, ticket T-105, message M7, decision DEC-5)."""
    ctx = Ctx()
    rows = ctx.run("MATCH (n) WHERE n.id = $id RETURN labels(n)[0], n", id=item_id.strip().upper())
    if not rows:
        return ctx.result({"error": f"No item with id {item_id}"})
    label, node = rows[0]
    props = dict(node.properties)
    ctx.run("MATCH p=(n)-[r]-(m) WHERE n.id = $id RETURN p", id=item_id.strip().upper())
    return ctx.result({"type": label, **props})


def who_knows(topic: str):
    """Rank the people who know most about a system or project, using the graph around it."""
    ctx = Ctx()
    ent = _resolve(ctx, topic)
    if not ent:
        return ctx.result(_not_found(topic))
    label, name = ent
    people = _score_people(ctx, name, label)
    ranked = sorted(people.values(), key=lambda p: -p["score"])
    return ctx.result({"topic": name, "type": label, "ranked_people": ranked[:6],
                       "note": "score = weighted evidence (authored docs/decisions/leading=3, tickets=2, "
                               "team membership/messages=1). status 'left' means the person left the company."})


def _score_people(ctx, name, label):
    people = defaultdict(lambda: {"name": "", "role": "", "status": "", "score": 0, "evidence": []})

    def add(person, role, status, weight, evidence):
        p = people[person]
        p["name"], p["role"], p["status"] = person, role, status
        p["score"] += weight
        p["evidence"].append(evidence)

    for r in ctx.run("MATCH p=(per:Person)-[:AUTHORED]->(d:Document)-[:ABOUT]->(t {name:$n}) "
                     "RETURN per.name, per.role, per.status, d.id, d.title, p", n=name):
        add(r[0], r[1], r[2], 3, f"authored {r[3]} '{r[4]}'")
    for r in ctx.run("MATCH p=(d:Decision)-[:CONCERNS]->(t {name:$n}), (d)-[:DECIDED_BY]->(per:Person) "
                     "RETURN per.name, per.role, per.status, d.id, d.title, p", n=name):
        add(r[0], r[1], r[2], 3, f"decided {r[3]} '{r[4]}'")
    for r in ctx.run("MATCH p=(k:Ticket)-[:AFFECTS]->(t {name:$n}), (k)-[:ASSIGNED_TO]->(per:Person) "
                     "RETURN per.name, per.role, per.status, k.id, k.title, k.status, p", n=name):
        add(r[0], r[1], r[2], 2, f"assigned {r[3]} '{r[4]}' ({r[5]})")
    for r in ctx.run("MATCH p=(per:Person)-[:POSTED]->(m:Message)-[:MENTIONS]->(t {name:$n}) "
                     "RETURN per.name, per.role, per.status, m.id, p", n=name):
        add(r[0], r[1], r[2], 1, f"posted message {r[3]}")
    if label == "Project":
        for r in ctx.run("MATCH p=(per:Person)-[:LEADS]->(t:Project {name:$n}) "
                         "RETURN per.name, per.role, per.status, p", n=name):
            add(r[0], r[1], r[2], 3, "leads this project")
    else:
        for r in ctx.run("MATCH p=(per:Person)-[:LEADS]->(pr:Project)-[:USES]->(t:System {name:$n}) "
                         "RETURN per.name, per.role, per.status, pr.name, p", n=name):
            add(r[0], r[1], r[2], 3, f"leads {r[3]} which uses it")
        for r in ctx.run("MATCH p=(per:Person)-[:MEMBER_OF]->(tm:Team)-[:OWNS]->(t:System {name:$n}) "
                         "RETURN per.name, per.role, per.status, tm.name, p", n=name):
            add(r[0], r[1], r[2], 1, f"member of owning team {r[3]}")
    return people


def ownership(name: str):
    """Who owns a system or project: owning team, members, project leads, related docs."""
    ctx = Ctx()
    ent = _resolve(ctx, name)
    if not ent:
        return ctx.result(_not_found(name))
    label, n = ent
    data = {"name": n, "type": label}
    if label == "System":
        rows = ctx.run("MATCH p=(m:Person)-[:MEMBER_OF]->(t:Team)-[:OWNS]->(s:System {name:$n}) "
                       "RETURN t.name, m.name, m.role, m.status, p", n=n)
        data["owning_team"] = rows[0][0] if rows else None
        data["team_members"] = [{"name": r[1], "role": r[2], "status": r[3]} for r in rows]
        rows = ctx.run("MATCH p=(l:Person)-[:LEADS]->(pr:Project)-[:USES]->(s:System {name:$n}) "
                       "RETURN pr.name, l.name, l.status, p", n=n)
        data["projects_using_it"] = [{"project": r[0], "lead": r[1], "lead_status": r[2]} for r in rows]
        rows = ctx.run("MATCH p=(s:System {name:$n})-[:DEPENDS_ON]->(d:System) RETURN d.name, p", n=n)
        data["depends_on"] = [r[0] for r in rows]
    else:
        rows = ctx.run("MATCH p=(l:Person)-[:LEADS]->(pr:Project {name:$n}) "
                       "RETURN l.name, l.role, l.status, pr.description, p", n=n)
        if rows:
            data["lead"] = {"name": rows[0][0], "role": rows[0][1], "status": rows[0][2]}
            data["description"] = rows[0][3]
        rows = ctx.run("MATCH p=(pr:Project {name:$n})-[:USES]->(s:System) RETURN s.name, p", n=n)
        data["systems_used"] = [r[0] for r in rows]
    rows = ctx.run("MATCH p=(a:Person)-[:AUTHORED]->(d:Document)-[:ABOUT]->(t {name:$n}) "
                   "RETURN d.id, d.title, a.name, p", n=n)
    data["documents"] = [{"id": r[0], "title": r[1], "author": r[2]} for r in rows]
    return ctx.result(data)


def decision_history(topic: str):
    """Decisions about a topic, why they were made, their sources, and what they replaced."""
    ctx = Ctx()
    ks = _variants(topic)
    ids = set()
    for r in ctx.run("MATCH (d:Decision)-[:CONCERNS]->(t) WHERE toLower(t.name) IN $ks "
                     "OR toLower(t.name) CONTAINS $k0 OR toLower(t.name) CONTAINS $k1 RETURN d.id",
                     ks=ks, k0=ks[0], k1=ks[-1]):
        ids.add(r[0])
    for r in ctx.run("MATCH (d:Decision) WHERE toLower(d.title) CONTAINS $k0 OR toLower(d.title) CONTAINS $k1 "
                     "OR toLower(d.rationale) CONTAINS $k0 OR toLower(d.rationale) CONTAINS $k1 RETURN d.id",
                     k0=ks[0], k1=ks[-1]):
        ids.add(r[0])
    if not ids:
        return ctx.result({"error": f"No decisions found about '{topic}'. Try search first."})

    for _ in range(2):  # follow SUPERSEDES links both ways so the whole chain is shown
        for r in ctx.run("MATCH (a:Decision)-[:SUPERSEDES]->(b:Decision) "
                         "WHERE a.id IN $ids OR b.id IN $ids RETURN a.id, b.id", ids=list(ids)):
            ids.update([r[0], r[1]])
    ids = list(ids)

    supersedes = {}
    for r in ctx.run("MATCH p=(a:Decision)-[:SUPERSEDES]->(b:Decision) "
                     "WHERE a.id IN $ids RETURN a.id, b.id, p", ids=ids):
        supersedes[r[0]] = r[1]
    superseded_by = {old: new for new, old in supersedes.items()}

    decisions = {}
    for r in ctx.run("MATCH p=(d:Decision)-[:DECIDED_BY]->(per:Person) WHERE d.id IN $ids "
                     "RETURN d.id, d.title, d.date, d.rationale, per.name, per.status, p", ids=ids):
        decisions[r[0]] = {"id": r[0], "title": r[1], "date": r[2], "why": r[3],
                           "decided_by": r[4], "decider_status": r[5], "sources": [],
                           "replaces": supersedes.get(r[0]),
                           "status": f"superseded by {superseded_by[r[0]]}" if r[0] in superseded_by
                           else "current"}
    for r in ctx.run("MATCH p=(d:Decision)-[:SOURCED_FROM]->(s) WHERE d.id IN $ids "
                     "RETURN d.id, labels(s)[0], s.id, coalesce(s.title, s.text), p", ids=ids):
        decisions[r[0]]["sources"].append({"type": r[1], "id": r[2], "text": str(r[3])[:140]})
    ctx.run("MATCH p=(d:Decision)-[:CONCERNS]->(t) WHERE d.id IN $ids RETURN p", ids=ids)
    return ctx.result(sorted(decisions.values(), key=lambda d: d["date"]))


def impact_analysis(system: str):
    """What depends on a system (directly and indirectly), who owns those, and what is at risk."""
    ctx = Ctx()
    ent = _resolve(ctx, system, labels=("System",))
    if not ent:
        return ctx.result(_not_found(system, "System"))
    name = ent[1]
    depth = {}
    for r in ctx.run("MATCH p=(x:System)-[:DEPENDS_ON*1..4]->(s:System {name:$n}) "
                     "RETURN x.name, length(p), p", n=name):
        depth[r[0]] = min(depth.get(r[0], 99), r[1])
    affected = list(depth)
    owners = {}
    if affected:
        for r in ctx.run("MATCH p=(t:Team)-[:OWNS]->(x:System) WHERE x.name IN $ns RETURN x.name, t.name, p",
                         ns=affected):
            owners[r[0]] = r[1]
    names = affected + [name]
    projects = []
    for r in ctx.run("MATCH p=(l:Person)-[:LEADS]->(pr:Project)-[:USES]->(x:System) WHERE x.name IN $ns "
                     "RETURN pr.name, x.name, l.name, p", ns=names):
        projects.append({"project": r[0], "uses": r[1], "lead": r[2]})
    tickets = [{"id": r[0], "title": r[1], "status": r[2]} for r in ctx.run(
        "MATCH p=(k:Ticket)-[:AFFECTS]->(s:System {name:$n}) WHERE k.status <> 'closed' "
        "RETURN k.id, k.title, k.status, p", n=name)]
    return ctx.result({
        "system": name,
        "directly_affected": [s for s, d in depth.items() if d == 1],
        "indirectly_affected": [s for s, d in depth.items() if d > 1],
        "owners_of_affected_systems": owners,
        "projects_at_risk": projects,
        "open_tickets_on_this_system": tickets})


def incident_response(system: str):
    """Use graph ownership and dependency data to propose a follow-up incident ticket."""
    ctx = Ctx()
    ent = _resolve(ctx, system, labels=("System",))
    if not ent:
        return ctx.result(_not_found(system, "System"))
    name = ent[1]
    owners = []
    for r in ctx.run("MATCH p=(per:Person {status:'active'})-[:MEMBER_OF]->(team:Team)-[:OWNS]->"
                     "(s:System {name:$n}) RETURN per.name, per.role, team.name, p ORDER BY per.name", n=name):
        owners.append({"name": r[0], "role": r[1], "team": r[2]})
    affected = []
    for r in ctx.run("MATCH p=(x:System)-[:DEPENDS_ON*1..4]->(s:System {name:$n}) "
                     "RETURN DISTINCT x.name, p", n=name):
        affected.append(r[0])
    priority = "critical" if len(affected) >= 3 else "high"
    suggested_assignee = owners[0]["name"] if owners else None
    return ctx.result({
        "system": name,
        "affected_systems": affected,
        "available_assignees": owners,
        "recommended_action": {
            "action": "create_followup_ticket",
            "title": f"Investigate {name} incident and coordinate recovery",
            "suggested_assignee": suggested_assignee,
            "priority": priority,
            "reason": f"{len(affected)} system(s) depend on {name}."}}
    )


def find_connection(a: str, b: str):
    """Shortest path between any two things in the graph (people, systems, documents, ...)."""
    ctx = Ctx()

    def find(text):
        ks = _variants(text)
        rows = ctx.run(
            "MATCH (n) WHERE toLower(coalesce(n.name,'')) IN $ks OR toLower(coalesce(n.id,'')) IN $ks "
            "OR toLower(coalesce(n.name,'')) CONTAINS $k0 OR toLower(coalesce(n.title,'')) CONTAINS $k0 "
            "RETURN ID(n), coalesce(n.name, n.title, n.id) ORDER BY size(coalesce(n.name, n.title, n.id)) LIMIT 1",
            ks=ks, k0=ks[0])
        return rows[0] if rows else None

    na, nb = find(a), find(b)
    if not na or not nb:
        return ctx.result({"error": f"Could not find {'both things' if not na and not nb else (a if not na else b)}."})
    def label_of(n):
        p = n.properties
        return p.get("name") or p.get("title") or p.get("id")

    # FalkorDB's shortestPath needs a direction, so try both ways first
    for src, dst in ((na[0], nb[0]), (nb[0], na[0])):
        rows = ctx.run("MATCH (x), (y) WHERE ID(x) = $ia AND ID(y) = $ib "
                       "WITH shortestPath((x)-[*..6]->(y)) AS p RETURN p, length(p)", ia=src, ib=dst)
        rows = [r for r in rows if r[0] is not None]
        if rows:
            path = rows[0][0]
            nodes = path.nodes()
            hops = [f"{label_of(nodes[i])} -[{e.relation}]-> {label_of(nodes[i + 1])}"
                    for i, e in enumerate(path.edges())]
            return ctx.result({"from": na[1], "to": nb[1], "length": rows[0][1], "path": hops})

    # No directed path: breadth-first search ignoring direction over the edge list
    edges = ctx.run("MATCH (a)-[r]->(b) RETURN ID(a), ID(b), type(r)")
    adj = defaultdict(list)
    for s_, d_, t_ in edges:
        adj[s_].append((d_, t_, True))
        adj[d_].append((s_, t_, False))
    prev, queue = {na[0]: None}, [na[0]]
    while queue and nb[0] not in prev:
        cur = queue.pop(0)
        for nxt, typ, fwd in adj[cur]:
            if nxt not in prev:
                prev[nxt] = (cur, typ, fwd)
                queue.append(nxt)
    if nb[0] not in prev:
        return ctx.result({"from": na[1], "to": nb[1], "connected": False})
    steps, cur = [], nb[0]
    while prev[cur]:
        steps.append((prev[cur][0], cur, prev[cur][1], prev[cur][2]))
        cur = prev[cur][0]
    steps.reverse()
    ids = [na[0]] + [s_[1] for s_ in steps]
    names = {r[0]: r[1] for r in ctx.run(
        "MATCH (n) WHERE ID(n) IN $ids RETURN ID(n), coalesce(n.name, n.title, n.id), n", ids=ids)}
    hops = []
    for a_, b_, typ, fwd in steps:
        hops.append(f"{names[a_]} -[{typ}]-> {names[b_]}" if fwd else f"{names[a_]} <-[{typ}]- {names[b_]}")
        src, dst = (a_, b_) if fwd else (b_, a_)
        ctx.edges[(src, dst, typ)] = {"from": src, "to": dst, "label": typ}
    return ctx.result({"from": na[1], "to": nb[1], "length": len(steps), "path": hops})


def key_systems():
    """Rank systems by how much the rest of the company depends on them (PageRank)."""
    ctx = Ctx()
    rows = ctx.run("CALL algo.pageRank('System', 'DEPENDS_ON') YIELD node, score "
                   "RETURN node.name, score ORDER BY score DESC LIMIT 6")
    ctx.run("MATCH p=(a:System)-[:DEPENDS_ON]->(b:System) RETURN p")
    return ctx.result([{"system": r[0], "importance_score": round(r[1], 3)} for r in rows])


def knowledge_risk():
    """Find systems whose knowledge sits with people who have left the company."""
    ctx = Ctx()
    systems = [r[0] for r in ctx.run("MATCH (s:System) RETURN s.name")]
    out = []
    for s in systems:
        people = _score_people(Ctx(), s, "System")
        total = sum(p["score"] for p in people.values()) or 1
        left = [p for p in people.values() if p["status"] == "left"]
        left_score = sum(p["score"] for p in left)
        if not left:
            continue
        active = sorted((p for p in people.values() if p["status"] != "left"), key=lambda p: -p["score"])
        docs = Ctx().run(
            "MATCH (per:Person)-[:AUTHORED]->(d:Document)-[:ABOUT]->(s:System {name:$n}) "
            "RETURN d.id, d.title, d.kind, per.status", n=s)
        active_kinds = {r[2] for r in docs if r[3] != "left"}
        orphaned = [f"{r[0]} '{r[1]}'" for r in docs if r[3] == "left" and r[2] not in active_kinds]
        out.append({"system": s, "share_of_knowledge_held_by_departed": round(left_score / total, 2),
                    "documents_by_departed_with_no_active_equivalent": orphaned,
                    "departed": [{"name": p["name"], "evidence": p["evidence"]} for p in left],
                    "best_remaining_expert": active[0]["name"] if active else None})
    out.sort(key=lambda r: -r["share_of_knowledge_held_by_departed"])
    ctx.run("MATCH p=(per:Person {status:'left'})-[:AUTHORED]->(d:Document)-[:ABOUT]->(s:System) RETURN p")
    ctx.run("MATCH p=(per:Person {status:'left'})-[:POSTED]->(m:Message)-[:MENTIONS]->(s:System) RETURN p")
    return ctx.result(out)


# ------------------------------------------------- write path (used by the UI form, not by the LLM)
AGENT_ROLES = (
    ("AGENT-INVESTIGATOR", "Incident Investigator", "Finds impact and saves incident findings."),
    ("AGENT-COORDINATOR", "Response Coordinator", "Reads handoffs and creates recovery work."),
)


def _ensure_coordination_agents(g):
    for agent_id, name, role in AGENT_ROLES:
        g.query("MERGE (a:Agent {id:$id}) SET a.name=$name, a.role=$role",
                {"id": agent_id, "name": name, "role": role})


def _next_id(g, label, prefix):
    count = g.query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
    return f"{prefix}-{count + 1}"


def incident_context(incident_id):
    """Read shared memory, handoffs, tickets and graph paths for one incident."""
    ctx = Ctx()
    rows = ctx.run("MATCH p=(i:Incident {id:$id})-[:CONCERNS]->(s:System) RETURN i.status, s.name, p",
                   id=incident_id)
    if not rows:
        return ctx.result({"error": f"No incident with id {incident_id}."})
    memories = [{"agent": r[0], "id": r[1], "text": r[2]} for r in ctx.run(
        "MATCH p=(a:Agent)-[:WROTE]->(m:Memory)-[:ABOUT]->(i:Incident {id:$id}) "
        "RETURN a.name, m.id, m.text, p ORDER BY m.id", id=incident_id)]
    handoffs = [{"from": r[0], "to": r[1], "note": r[2]} for r in ctx.run(
        "MATCH p=(a:Agent)-[:HANDED_OFF]->(h:Handoff)-[:FOR_INCIDENT]->(i:Incident {id:$id}), "
        "(h)-[:TO]->(b:Agent) RETURN a.name, b.name, h.note, p ORDER BY h.id", id=incident_id)]
    tickets = [{"id": r[0], "title": r[1], "status": r[2]} for r in ctx.run(
        "MATCH p=(i:Incident {id:$id})-[:HAS_TICKET]->(t:Ticket) RETURN t.id, t.title, t.status, p",
        id=incident_id)]
    return ctx.result({"id": incident_id, "status": rows[0][0], "system": rows[0][1],
                       "memories": memories, "handoffs": handoffs, "tickets": tickets})


def start_incident_investigation(system, summary=None):
    """Investigator agent stores graph-derived incident findings as shared memory."""
    plan = incident_response(system)["data"]
    if "error" in plan:
        raise ValueError(plan["error"])
    g = get_graph()
    _ensure_coordination_agents(g)
    incident_id, memory_id = _next_id(g, "Incident", "INC"), _next_id(g, "Memory", "MEM")
    affected = ", ".join(plan["affected_systems"]) or "no downstream systems"
    summary = summary or (f"Investigated {plan['system']}: affected systems are {affected}. "
                          f"Recommended priority is {plan['recommended_action']['priority']}.")
    g.query("MATCH (s:System {name:$system}), (a:Agent {id:'AGENT-INVESTIGATOR'}) "
            "CREATE (i:Incident {id:$incident, status:'investigating'})-[:CONCERNS]->(s) "
            "CREATE (m:Memory {id:$memory, text:$text, kind:'investigation'}) "
            "CREATE (a)-[:WROTE]->(m)-[:ABOUT]->(i)",
            {"system": plan["system"], "incident": incident_id, "memory": memory_id, "text": summary})
    return incident_context(incident_id)


def handoff_incident(incident_id, note="Investigation complete; please coordinate recovery."):
    """Persist a handoff from the investigator agent to the response coordinator."""
    g = get_graph()
    _ensure_coordination_agents(g)
    if not g.query("MATCH (i:Incident {id:$id}) RETURN i", {"id": incident_id}).result_set:
        raise ValueError(f"No incident with id {incident_id}.")
    handoff_id = _next_id(g, "Handoff", "HANDOFF")
    g.query("MATCH (i:Incident {id:$incident}), (a:Agent {id:'AGENT-INVESTIGATOR'}), "
            "(b:Agent {id:'AGENT-COORDINATOR'}) "
            "SET i.status='handed_off' "
            "CREATE (h:Handoff {id:$id, note:$note}) "
            "CREATE (a)-[:HANDED_OFF]->(h)-[:FOR_INCIDENT]->(i) "
            "CREATE (h)-[:TO]->(b)", {"incident": incident_id, "id": handoff_id, "note": note})
    return incident_context(incident_id)


def coordinator_create_followup(incident_id, decision=None):
    """Coordinator reads shared incident memory and creates the recommended ticket."""
    context = incident_context(incident_id)["data"]
    if "error" in context:
        raise ValueError(context["error"])
    if context["tickets"]:
        return incident_context(incident_id)
    plan = incident_response(context["system"])["data"]["recommended_action"]
    if not plan["suggested_assignee"]:
        raise ValueError("No active owner is available for this incident.")
    ticket = create_incident_followup(context["system"], plan["title"], plan["suggested_assignee"], plan["priority"])
    g = get_graph()
    memory_id = _next_id(g, "Memory", "MEM")
    text = decision or f"Read shared investigation memory and created {ticket['id']} for {ticket['assignee']}."
    g.query("MATCH (i:Incident {id:$incident}), (t:Ticket {id:$ticket}), "
            "(a:Agent {id:'AGENT-COORDINATOR'}) SET i.status='responding' "
            "MERGE (i)-[:HAS_TICKET]->(t) "
            "CREATE (m:Memory {id:$memory, text:$text, kind:'coordination'}) "
            "CREATE (a)-[:WROTE]->(m)-[:ABOUT]->(i)",
            {"incident": incident_id, "ticket": ticket["id"], "memory": memory_id, "text": text})
    return incident_context(incident_id)


def list_incidents():
    """List persisted incidents so a later session can continue their handoff."""
    rows = get_graph().query("MATCH (i:Incident)-[:CONCERNS]->(s:System) "
                             "RETURN i.id, i.status, s.name ORDER BY i.id DESC").result_set
    return [{"id": row[0], "status": row[1], "system": row[2]} for row in rows]


def create_incident_followup(system, title, assignee, priority="high", date=None):
    """Create a graph-backed incident follow-up ticket after user confirmation in the UI."""
    if priority not in ("medium", "high", "critical"):
        raise ValueError("Priority must be medium, high, or critical.")
    ctx = Ctx()
    ent = _resolve(ctx, system, labels=("System",))
    if not ent:
        raise ValueError(_not_found(system, "System")["error"])
    name = ent[1]
    g = get_graph()
    active = g.query("MATCH (p:Person {name:$name, status:'active'}) RETURN p", {"name": assignee}).result_set
    if not active:
        raise ValueError(f"'{assignee}' is not an active person in the graph.")
    ticket_ids = [r[0] for r in g.query("MATCH (k:Ticket) RETURN k.id").result_set]
    numbers = [int(ticket_id[2:]) for ticket_id in ticket_ids
               if isinstance(ticket_id, str) and ticket_id.startswith("T-") and ticket_id[2:].isdigit()]
    new_id = f"T-{max(numbers, default=100) + 1}"
    if date is None:
        import datetime
        date = datetime.date.today().isoformat()
    g.query("MATCH (p:Person {name:$assignee}), (s:System {name:$system}) "
            "CREATE (k:Ticket {id:$id, title:$title, status:'open', created:$date, priority:$priority, "
            "source:'incident_response'})-[:ASSIGNED_TO]->(p) "
            "CREATE (k)-[:AFFECTS]->(s)",
            {"id": new_id, "title": title, "assignee": assignee, "system": name,
             "date": date, "priority": priority})
    return {"id": new_id, "system": name, "assignee": assignee, "priority": priority}


def add_decision(title, rationale, decided_by, concerns, supersedes_id=None, date=None):
    """Log a new decision so the graph stays current. Returns the new decision id."""
    import datetime
    g = get_graph()
    n = g.query("MATCH (d:Decision) RETURN count(d)").result_set[0][0]
    new_id = f"DEC-{n + 1}"
    date = date or datetime.date.today().isoformat()
    g.query("MATCH (p:Person {name:$by}) CREATE (d:Decision {id:$id, title:$t, date:$date, rationale:$r}) "
            "CREATE (d)-[:DECIDED_BY]->(p)",
            {"by": decided_by, "id": new_id, "t": title, "date": date, "r": rationale})
    for name in concerns:
        g.query("MATCH (d:Decision {id:$id}), (t) WHERE (t:System OR t:Project) AND t.name = $n "
                "MERGE (d)-[:CONCERNS]->(t)", {"id": new_id, "n": name})
    if supersedes_id:
        g.query("MATCH (new:Decision {id:$new}), (old:Decision {id:$old}) MERGE (new)-[:SUPERSEDES]->(old)",
                {"new": new_id, "old": supersedes_id})
    return new_id


def import_records(records):
    """Create or update Documents, Tickets and Messages imported from the sidebar.

    Each record must have ``type`` set to Document, Ticket or Message. Existing
    records with the same ``id`` are updated in place, so the graph has one
    current version of each company record.
    """
    if not isinstance(records, list) or not records:
        raise ValueError("Upload a JSON array with at least one record.")

    g = get_graph()
    imported = []

    def required(record, fields):
        missing = [field for field in fields if not record.get(field)]
        if missing:
            raise ValueError(f"Record {record.get('id', '<no id>')} is missing: {', '.join(missing)}")

    def person_exists(name):
        return bool(g.query("MATCH (p:Person {name:$n}) RETURN p LIMIT 1", {"n": name}).result_set)

    def entity_exists(label, name):
        if label not in ("System", "Project"):
            raise ValueError("Related entities must use System or Project labels.")
        return bool(g.query(f"MATCH (n:{label} {{name:$n}}) RETURN n LIMIT 1", {"n": name}).result_set)

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Every imported item must be a JSON object.")
        kind = record.get("type")
        if kind == "Document":
            required(record, ("id", "title", "kind", "date", "author", "summary"))
            if not person_exists(record["author"]):
                raise ValueError(f"Document {record['id']}: author '{record['author']}' is not in the graph.")
            about = record.get("about", [])
            for target in about:
                if not entity_exists(target.get("label"), target.get("name")):
                    raise ValueError(f"Document {record['id']}: unknown related entity {target}.")
            g.query(
                "MERGE (d:Document {id:$id}) "
                "SET d.title=$title, d.kind=$kind, d.date=$date, d.summary=$summary "
                "WITH d MATCH (d)-[old:AUTHORED]->() DELETE old "
                "WITH d MATCH (a:Person {name:$author}) MERGE (a)-[:AUTHORED]->(d)",
                {key: record[key] for key in ("id", "title", "kind", "date", "summary")} | {"author": record["author"]})
            g.query("MATCH (d:Document {id:$id})-[old:ABOUT]->() DELETE old", {"id": record["id"]})
            for target in about:
                g.query(f"MATCH (d:Document {{id:$id}}), (t:{target['label']} {{name:$name}}) "
                        "MERGE (d)-[:ABOUT]->(t)", {"id": record["id"], "name": target["name"]})
        elif kind == "Ticket":
            required(record, ("id", "title", "status", "assignee", "created"))
            if not person_exists(record["assignee"]):
                raise ValueError(f"Ticket {record['id']}: assignee '{record['assignee']}' is not in the graph.")
            affects = record.get("affects", [])
            for name in affects:
                if not entity_exists("System", name):
                    raise ValueError(f"Ticket {record['id']}: unknown system '{name}'.")
            g.query(
                "MERGE (k:Ticket {id:$id}) SET k.title=$title, k.status=$status, k.created=$created "
                "WITH k MATCH (k)-[old:ASSIGNED_TO]->() DELETE old "
                "WITH k MATCH (a:Person {name:$assignee}) MERGE (k)-[:ASSIGNED_TO]->(a)",
                {key: record[key] for key in ("id", "title", "status", "created")} | {"assignee": record["assignee"]})
            g.query("MATCH (k:Ticket {id:$id})-[old:AFFECTS]->() DELETE old", {"id": record["id"]})
            for name in affects:
                g.query("MATCH (k:Ticket {id:$id}), (s:System {name:$name}) MERGE (k)-[:AFFECTS]->(s)",
                        {"id": record["id"], "name": name})
        elif kind == "Message":
            required(record, ("id", "channel", "author", "date", "text"))
            if not person_exists(record["author"]):
                raise ValueError(f"Message {record['id']}: author '{record['author']}' is not in the graph.")
            mentions = record.get("mentions", [])
            for target in mentions:
                if not entity_exists(target.get("label"), target.get("name")):
                    raise ValueError(f"Message {record['id']}: unknown related entity {target}.")
            g.query(
                "MERGE (m:Message {id:$id}) SET m.channel=$channel, m.date=$date, m.text=$text "
                "WITH m MATCH (m)-[old:POSTED]->() DELETE old "
                "WITH m MATCH (a:Person {name:$author}) MERGE (a)-[:POSTED]->(m)",
                {key: record[key] for key in ("id", "channel", "date", "text")} | {"author": record["author"]})
            g.query("MATCH (m:Message {id:$id})-[old:MENTIONS]->() DELETE old", {"id": record["id"]})
            for target in mentions:
                g.query(f"MATCH (m:Message {{id:$id}}), (t:{target['label']} {{name:$name}}) "
                        "MERGE (m)-[:MENTIONS]->(t)", {"id": record["id"], "name": target["name"]})
        else:
            raise ValueError(f"Record {record.get('id', '<no id>')}: type must be Document, Ticket, or Message.")
        imported.append({"id": record["id"], "type": kind})
    return imported


# ------------------------------------------------- registry + schemas for the LLM
FUNCTIONS = {f.__name__: f for f in (search, get_item, who_knows, ownership, decision_history,
                                     impact_analysis, incident_response, find_connection, key_systems,
                                     knowledge_risk)}


def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


STR = {"type": "string"}
TOOL_SCHEMAS = [
    _fn("search", "Keyword search over names, titles, message text and rationales. Use when unsure what exists.",
        {"keyword": STR}, ["keyword"]),
    _fn("get_item", "Read the full text of a source item by id: document (D3), ticket (T-105), message (M7), "
        "decision (DEC-5).", {"item_id": STR}, ["item_id"]),
    _fn("who_knows", "Rank who knows most about a system or project, with evidence. Flags people who left.",
        {"topic": STR}, ["topic"]),
    _fn("ownership", "Who owns a system or project: owning team, members, project leads, documents.",
        {"name": STR}, ["name"]),
    _fn("decision_history", "Decisions about a topic: why they were made, who decided, sources, and which "
        "decision replaced which. Use for 'why did we...' questions.", {"topic": STR}, ["topic"]),
    _fn("impact_analysis", "What breaks if a system goes down: dependent systems, their owners, projects at "
        "risk, open tickets.", {"system": STR}, ["system"]),
    _fn("incident_response", "Recommend a graph-backed incident response: affected systems, active owners, "
        "suggested assignee and priority. Use for questions about what to do after a system failure.",
        {"system": STR}, ["system"]),
    _fn("find_connection", "Shortest path between any two entities in the company graph.",
        {"a": STR, "b": STR}, ["a", "b"]),
    _fn("key_systems", "Rank the most critical systems by dependency (PageRank graph algorithm)."),
    _fn("knowledge_risk", "Find systems whose knowledge is held by people who have left (bus-factor risk)."),
]
