"""The Company Brain agent: an LLM that answers by calling graph tools.

Works with any OpenAI-compatible endpoint. Defaults to Groq or Gemini (both have free tiers):
  LLM_PROVIDER=groq    + GROQ_API_KEY=...
  LLM_PROVIDER=gemini  + GEMINI_API_KEY=...
Override the model with LLM_MODEL.
"""
import json
import os

from dotenv import load_dotenv
from openai import OpenAI

import tools

load_dotenv()

PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key": "GROQ_API_KEY",
             "model": "openai/gpt-oss-20b"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key": "GEMINI_API_KEY", "model": "gemini-2.5-flash"},
}

SYSTEM_PROMPT = """You are Company Brain, an assistant that knows how Nimbus Labs works.
All company knowledge lives in a graph database (people, teams, systems, projects, documents,
tickets, chat messages and decisions). You can ONLY learn facts by calling the tools.

Rules:
- Always call a tool before answering a factual question. Never guess or use outside knowledge.
- Pick the tool that matches the question: decision_history for "why did we...", who_knows for
  "who knows/can help with...", impact_analysis for "what breaks if...", ownership for "who owns...",
  knowledge_risk for people-who-left questions, incident_response for "what should we do if..." or
  incident follow-up questions, find_connection for "how is X related to Y".
- Pass concise canonical graph topics to tools (for example, "event bus", not "event bus migration").
- If a tool reports no match, retry with the key entity or search its exact name before concluding no data exists.
- If a result names a source id, read it with get_item when the exact wording matters.
- Cite sources inline with their ids in square brackets, like [DEC-5], [D4], [T-101], [M3].
- Say clearly when a person has left the company (status 'left') or a decision was superseded.
- If the tools return nothing useful, say so plainly. Keep answers short and concrete.
"""

INVESTIGATOR_PROMPT = """You are the Incident Investigator for Nimbus Labs.
You receive verified graph evidence about a system failure. Write a concise incident report for the
Response Coordinator. Mention affected systems, people or teams to involve, and the recommended priority.
Use only the supplied evidence. Do not invent facts, use markdown headings, or exceed 120 words."""

COORDINATOR_PROMPT = """You are the Response Coordinator for Nimbus Labs.
You receive shared incident memory that was saved by the Incident Investigator. Write a concise recovery
decision for the incident record: state why a follow-up ticket is needed, its suggested owner and priority.
Use only the supplied graph context. Do not invent facts, use markdown headings, or exceed 120 words."""


def _client():
    name = os.getenv("LLM_PROVIDER", "groq" if os.getenv("GROQ_API_KEY") else "gemini").lower()
    cfg = PROVIDERS[name]
    key = os.getenv(cfg["key"])
    if not key:
        raise RuntimeError(f"Missing {cfg['key']}. Copy .env.example to .env and add your key.")
    return OpenAI(api_key=key, base_url=cfg["base_url"]), os.getenv("LLM_MODEL", cfg["model"]), name


def ask(question: str, history=None, max_steps: int = 6):
    """Returns {"answer": str, "trace": [tool-call dicts], "graph": merged subgraph}."""
    client, model, _ = _client()
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-6:]:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    trace, nodes, edges = [], {}, {}
    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=tools.TOOL_SCHEMAS, tool_choice="auto", temperature=0.1)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return {"answer": msg.content or "(no answer)", "trace": trace,
                    "graph": {"nodes": list(nodes.values()), "edges": list(edges.values())}}

        calls = []
        for tc in msg.tool_calls:
            d = {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
            extra = getattr(tc, "extra_content", None) or (getattr(tc, "model_extra", None) or {}).get("extra_content")
            if extra:
                d["extra_content"] = extra
            calls.append(d)
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": calls})

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
                if name not in tools.FUNCTIONS:
                    raise ValueError(f"unknown tool {name}")
                result = tools.FUNCTIONS[name](**args)
            except Exception as e:  # report the problem to the model so it can recover
                result = {"data": {"error": str(e)}, "graph": {"nodes": [], "edges": []}, "cypher": []}
                args = {}
            for n in result["graph"]["nodes"]:
                nodes[n["id"]] = n
            for e in result["graph"]["edges"]:
                edges[(e["from"], e["to"], e["label"])] = e
            trace.append({"tool": name, "args": args, "cypher": result["cypher"],
                          "result_preview": json.dumps(result["data"], default=str)[:600],
                          "graph": result["graph"]})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result["data"], default=str)[:6000]})

    return {"answer": "I could not finish within the step limit. Try a more specific question.",
            "trace": trace, "graph": {"nodes": list(nodes.values()), "edges": list(edges.values())}}


def _role_summary(prompt, evidence, fallback):
    """Ask one role to summarise verified graph evidence; keep the graph workflow usable without an API key."""
    try:
        client, model, _ = _client()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "Verified graph evidence:\n" + json.dumps(evidence, default=str)},
            ],
            temperature=0.1,
        )
        summary = (response.choices[0].message.content or "").strip()
        if summary:
            return summary, True
    except Exception:
        pass
    return fallback, False


def investigate_incident(system):
    """Run the Investigator role, then persist its report as shared FalkorDB memory."""
    evidence = tools.incident_response(system)["data"]
    if "error" in evidence:
        raise ValueError(evidence["error"])
    action = evidence["recommended_action"]
    fallback = (f"Investigation for {evidence['system']}: affected systems are "
                f"{', '.join(evidence['affected_systems']) or 'none'}. "
                f"Involve {action['suggested_assignee'] or 'the system owner'}; "
                f"recommended priority is {action['priority']}." )
    report, used_llm = _role_summary(INVESTIGATOR_PROMPT, evidence, fallback)
    result = tools.start_incident_investigation(system, summary=report)
    return {"role": "Incident Investigator", "summary": report, "used_llm": used_llm,
            "result": result, "evidence": evidence}


def coordinate_incident(incident_id):
    """Run the Coordinator role against persisted incident memory, then create its follow-up ticket."""
    context = tools.incident_context(incident_id)["data"]
    if "error" in context:
        raise ValueError(context["error"])
    if context["tickets"]:
        return {"role": "Response Coordinator", "summary": "A follow-up ticket already exists for this incident.",
                "used_llm": False, "result": tools.incident_context(incident_id), "evidence": context}
    plan = tools.incident_response(context["system"])["data"]["recommended_action"]
    evidence = {"incident": context, "recommended_action": plan}
    fallback = (f"Shared investigation findings require follow-up work. Create the {plan['priority']} priority "
                f"ticket for {plan['suggested_assignee']}: {plan['title']}.")
    decision, used_llm = _role_summary(COORDINATOR_PROMPT, evidence, fallback)
    result = tools.coordinator_create_followup(incident_id, decision=decision)
    return {"role": "Response Coordinator", "summary": decision, "used_llm": used_llm,
            "result": result, "evidence": evidence}
