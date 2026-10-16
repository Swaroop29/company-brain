"""Quick check that every graph tool works. Run:  python smoke_test.py
(needs FalkorDB running and `python ingest.py` already done)."""
import json

import tools

CALLS = [
    ("search", {"keyword": "kafka"}),
    ("get_item", {"item_id": "DEC-5"}),
    ("who_knows", {"topic": "auth service"}),
    ("ownership", {"name": "event bus"}),
    ("ownership", {"name": "Project Atlas"}),
    ("decision_history", {"topic": "event bus"}),
    ("impact_analysis", {"system": "event-bus"}),
    ("find_connection", {"a": "Dana Whitfield", "b": "payments-service"}),
    ("key_systems", {}),
    ("knowledge_risk", {}),
]

failed = 0
for name, args in CALLS:
    try:
        r = tools.FUNCTIONS[name](**args)
        d = json.dumps(r["data"], default=str)
        flag = "ERR" if '"error"' in d[:20] else "ok "
        print(f"[{flag}] {name}({args}) -> {len(r['graph']['nodes'])} nodes, {len(r['cypher'])} queries")
        print("      ", d[:230])
    except Exception as e:
        failed += 1
        print(f"[FAIL] {name}({args}): {e}")
print("\nAll tools OK" if not failed else f"\n{failed} tool(s) failed")
