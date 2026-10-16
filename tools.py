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


# ------------------------------------------------- registry + schemas for the LLM
FUNCTIONS = {f.__name__: f for f in (search, get_item, who_knows, ownership, decision_history,
                                     impact_analysis, find_connection, key_systems, knowledge_risk)}


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
    _fn("find_connection", "Shortest path between any two entities in the company graph.",
        {"a": STR, "b": STR}, ["a", "b"]),
    _fn("key_systems", "Rank the most critical systems by dependency (PageRank graph algorithm)."),
    _fn("knowledge_risk", "Find systems whose knowledge is held by people who have left (bus-factor risk)."),
]
