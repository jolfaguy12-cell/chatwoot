"""LangGraph wiring: classify → agent ⇄ validate → finalize.

There is no order-authorization node here: a Basalam customer is already
identified by the chat they write in, so `get_my_order` resolves their order
without asking them for a phone number or an order id.
"""

from langgraph.graph import END, StateGraph

from app.agent import nodes
from app.agent.state import AgentState

_compiled = None


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("classify", nodes.classify_node)
    graph.add_node("agent", nodes.agent_node)
    graph.add_node("validate", nodes.validate_node)
    graph.add_node("finalize", nodes.finalize_node)

    graph.set_entry_point("classify")
    graph.add_conditional_edges("classify", nodes.route_after_classify,
                                {"agent": "agent", "finalize": "finalize"})
    graph.add_conditional_edges("agent", nodes.route_after_agent,
                                {"validate": "validate", "finalize": "finalize"})
    graph.add_conditional_edges("validate", nodes.route_after_validate,
                                {"agent": "agent", "finalize": "finalize"})
    graph.add_edge("finalize", END)
    return graph.compile()


def get_graph():
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled
