# Company Brain

An agent that understands how a (fictional) company, **Nimbus Labs**, works. People, teams, systems,
projects, documents, tickets, chat messages and decisions live in one **FalkorDB** graph. The agent can
only learn facts by running graph queries, and every answer shows the Cypher it ran and the path it followed.

Built for **Graph Hacks (FalkorDB x WeMakeDevs), Track 3: Company Brain**.

## What it can answer

| Question | Graph work |
|---|---|
| Why did we move the event bus from RabbitMQ to Kafka? | Follows `SUPERSEDES` and `SOURCED_FROM` edges from the decision back to the postmortem, RFC, Slack messages and ticket |
| Who knows how to rotate the signing keys, and are they still here? | Scores people by authored docs, decisions, tickets and messages around a system, and flags `status: left` |
| What breaks if the event bus goes down? | Variable-length `DEPENDS_ON*1..4` traversal, then owners, projects and open tickets |
| Which systems are at risk because people left? | Compares knowledge held by departed vs active people per system |
| How is Dana connected to payments-service? | `shortestPath` between any two nodes |
| Which systems are most critical? | `algo.pageRank` on the dependency graph |

New decisions can be logged from the sidebar, so the graph stays current and old decisions show as superseded.

## Graph data model

```
(Person)-[:MEMBER_OF]->(Team)-[:OWNS]->(System)-[:DEPENDS_ON]->(System)
(Person)-[:LEADS]->(Project)-[:USES]->(System)
(Person)-[:AUTHORED]->(Document)-[:ABOUT]->(System|Project)
(Ticket)-[:ASSIGNED_TO]->(Person)      (Ticket)-[:AFFECTS]->(System)
(Person)-[:POSTED]->(Message)-[:MENTIONS]->(System|Project)
(Decision)-[:DECIDED_BY]->(Person)     (Decision)-[:CONCERNS]->(System|Project)
(Decision)-[:SOURCED_FROM]->(Document|Ticket|Message)
(Decision)-[:SUPERSEDES]->(Decision)
```

About 75 nodes and 176 relationships, all synthetic (see `dataset.py`).

## Key Cypher and algorithms (all in `tools.py`)

```cypher
-- why a decision was made, with sources
MATCH p=(d:Decision)-[:SOURCED_FROM]->(s) WHERE d.id IN $ids RETURN d.id, labels(s)[0], s.id, p

-- what breaks if a system fails
MATCH p=(x:System)-[:DEPENDS_ON*1..4]->(s:System {name:$n}) RETURN x.name, length(p), p

-- who knows a system
MATCH p=(per:Person)-[:AUTHORED]->(d:Document)-[:ABOUT]->(t {name:$n}) RETURN per.name, per.status, d.id, p

-- shortest path between two entities
MATCH (x), (y) WHERE ID(x) = $ia AND ID(y) = $ib
WITH shortestPath((x)-[*..6]->(y)) AS p RETURN p, length(p)

-- critical systems
CALL algo.pageRank('System', 'DEPENDS_ON') YIELD node, score RETURN node.name, score ORDER BY score DESC
```

## Run it

1. Start FalkorDB:
   ```
   docker run -d --name falkordb -p 6379:6379 -p 3000:3000 -v falkordb_data:/var/lib/falkordb/data falkordb/falkordb:latest
   ```
2. Install and configure:
   ```
   pip install -r requirements.txt
   copy .env.example .env      (then add a free GROQ_API_KEY or GEMINI_API_KEY)
   ```
3. Check the graph tools (optional): `python ingest.py` then `python smoke_test.py`
4. Launch the app: `streamlit run app.py` (it loads the demo data automatically on first run)

Explore the raw graph at http://localhost:3000 (FalkorDB Browser, graph `companybrain`).

## Architecture

```
dataset.py -> ingest.py -> FalkorDB <- tools.py (Cypher) <- agent.py (LLM tool calling) <- app.py (Streamlit)
```

If you remove FalkorDB the app has nothing to answer from: the LLM has no company knowledge of its own and
is instructed to use only tool results.

## AI assistance disclosure

This project was built with the help of an AI coding assistant (Claude). The graph model, queries and
architecture were reviewed and are understood by the team.
