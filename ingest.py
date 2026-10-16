"""Load the synthetic Nimbus Labs data into FalkorDB as a graph.

Run:  python ingest.py
Safe to re-run: it wipes the graph first and rebuilds it from dataset.py.
"""
from db import get_graph
import dataset as ds

# Graph model
#   (Person)-[:MEMBER_OF]->(Team)-[:OWNS]->(System)-[:DEPENDS_ON]->(System)
#   (Person)-[:LEADS]->(Project)-[:USES]->(System)
#   (Person)-[:AUTHORED]->(Document)-[:ABOUT]->(System|Project)
#   (Ticket)-[:ASSIGNED_TO]->(Person), (Ticket)-[:AFFECTS]->(System)
#   (Person)-[:POSTED]->(Message)-[:MENTIONS]->(System|Project)
#   (Decision)-[:DECIDED_BY]->(Person), (Decision)-[:CONCERNS]->(System|Project)
#   (Decision)-[:SOURCED_FROM]->(Document|Ticket|Message), (Decision)-[:SUPERSEDES]->(Decision)


def ingest(reset: bool = True) -> dict:
    g = get_graph()
    if reset:
        try:
            g.delete()
        except Exception:
            pass  # graph did not exist yet
        g = get_graph()

    q = g.query

    for label, prop in [("Person", "name"), ("Person", "id"), ("System", "name"), ("Project", "name"),
                        ("Team", "name"), ("Document", "id"), ("Ticket", "id"), ("Message", "id"),
                        ("Decision", "id")]:
        try:
            q(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            pass  # index already exists

    for t in ds.TEAMS:
        q("MERGE (:Team {name:$n})", {"n": t})

    for pid, name, role, team, status in ds.PEOPLE:
        q("""MERGE (p:Person {id:$id})
             SET p.name=$name, p.role=$role, p.status=$status
             WITH p
             MATCH (t:Team {name:$team})
             MERGE (p)-[:MEMBER_OF]->(t)""",
          {"id": pid, "name": name, "role": role, "status": status, "team": team})

    for system, team in ds.SYSTEMS:
        q("""MERGE (s:System {name:$s})
             WITH s
             MATCH (t:Team {name:$t})
             MERGE (t)-[:OWNS]->(s)""", {"s": system, "t": team})

    for a, b in ds.DEPENDS_ON:
        q("""MATCH (a:System {name:$a}), (b:System {name:$b})
             MERGE (a)-[:DEPENDS_ON]->(b)""", {"a": a, "b": b})

    for name, status, lead, desc, systems in ds.PROJECTS:
        q("""MERGE (p:Project {name:$name}) SET p.status=$status, p.description=$desc
             WITH p
             MATCH (l:Person {id:$lead})
             MERGE (l)-[:LEADS]->(p)""",
          {"name": name, "status": status, "desc": desc, "lead": lead})
        for s in systems:
            q("""MATCH (p:Project {name:$p}), (s:System {name:$s})
                 MERGE (p)-[:USES]->(s)""", {"p": name, "s": s})

    for did, title, kind, date, author, summary, about in ds.DOCUMENTS:
        q("""MERGE (d:Document {id:$id})
             SET d.title=$title, d.kind=$kind, d.date=$date, d.summary=$summary
             WITH d
             MATCH (a:Person {id:$author})
             MERGE (a)-[:AUTHORED]->(d)""",
          {"id": did, "title": title, "kind": kind, "date": date, "summary": summary, "author": author})
        for label, name in about:
            q(f"""MATCH (d:Document {{id:$id}}), (t:{label} {{name:$n}})
                  MERGE (d)-[:ABOUT]->(t)""", {"id": did, "n": name})

    for tid, title, status, assignee, created, affects in ds.TICKETS:
        q("""MERGE (k:Ticket {id:$id})
             SET k.title=$title, k.status=$status, k.created=$created
             WITH k
             MATCH (p:Person {id:$a})
             MERGE (k)-[:ASSIGNED_TO]->(p)""",
          {"id": tid, "title": title, "status": status, "created": created, "a": assignee})
        for s in affects:
            q("""MATCH (k:Ticket {id:$id}), (s:System {name:$s})
                 MERGE (k)-[:AFFECTS]->(s)""", {"id": tid, "s": s})

    for mid, channel, author, date, text, mentions in ds.MESSAGES:
        q("""MERGE (m:Message {id:$id})
             SET m.channel=$channel, m.date=$date, m.text=$text
             WITH m
             MATCH (a:Person {id:$a})
             MERGE (a)-[:POSTED]->(m)""",
          {"id": mid, "channel": channel, "date": date, "text": text, "a": author})
        for label, name in mentions:
            q(f"""MATCH (m:Message {{id:$id}}), (t:{label} {{name:$n}})
                  MERGE (m)-[:MENTIONS]->(t)""", {"id": mid, "n": name})

    for did, title, date, by, rationale, sources, concerns, supersedes in ds.DECISIONS:
        q("""MERGE (d:Decision {id:$id})
             SET d.title=$title, d.date=$date, d.rationale=$rationale
             WITH d
             MATCH (p:Person {id:$by})
             MERGE (d)-[:DECIDED_BY]->(p)""",
          {"id": did, "title": title, "date": date, "rationale": rationale, "by": by})
        for label, sid in sources:
            q(f"""MATCH (d:Decision {{id:$id}}), (s:{label} {{id:$sid}})
                  MERGE (d)-[:SOURCED_FROM]->(s)""", {"id": did, "sid": sid})
        for label, name in concerns:
            q(f"""MATCH (d:Decision {{id:$id}}), (t:{label} {{name:$n}})
                  MERGE (d)-[:CONCERNS]->(t)""", {"id": did, "n": name})
        if supersedes:
            q("""MATCH (new:Decision {id:$new}), (old:Decision {id:$old})
                 MERGE (new)-[:SUPERSEDES]->(old)""", {"new": did, "old": supersedes})

    nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return {"nodes": nodes, "relationships": edges}


if __name__ == "__main__":
    stats = ingest()
    print(f"Loaded graph: {stats['nodes']} nodes, {stats['relationships']} relationships")
