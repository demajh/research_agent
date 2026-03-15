from __future__ import annotations

import logging
import operator
from typing import Annotated, Literal, TypedDict

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .config import AppConfig
from .nodes import WorkflowContext

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    run_id: str
    candidate_papers: list[dict]
    interest_reports: Annotated[list[dict], operator.add]
    email_subject: str
    email_html: str
    email_text: str


class InterestTaskState(TypedDict):
    run_id: str
    interest: dict
    candidate_papers: list[dict]


def build_graph(ctx: WorkflowContext, db_path: str = ".runs/checkpoints.db"):
    def fetch_candidates_node(state: AgentState) -> AgentState:
        return {"candidate_papers": ctx.fetch_candidates()}

    def fanout_interests(state: AgentState):
        sends = []
        for interest in ctx.cfg.interests:
            sends.append(
                Send(
                    "process_interest",
                    {
                        "run_id": state["run_id"],
                        "interest": interest.model_dump(mode="json"),
                        "candidate_papers": state.get("candidate_papers", []),
                    },
                )
            )
        return sends

    def process_interest_node(state: InterestTaskState) -> AgentState:
        from .config import InterestConfig

        interest = InterestConfig.model_validate(state["interest"])
        return ctx.process_interest(
            interest=interest,
            candidate_papers=state["candidate_papers"],
            run_id=state["run_id"],
        )

    def compose_email_node(state: AgentState) -> AgentState:
        payload = ctx.build_email_payload(state.get("interest_reports", []), state["run_id"])
        return {
            "email_subject": payload.subject,
            "email_html": payload.html_body,
            "email_text": payload.text_body,
        }

    def send_email_node(state: AgentState) -> AgentState:
        from .schemas import EmailPayload

        payload = EmailPayload(
            subject=state["email_subject"],
            html_body=state["email_html"],
            text_body=state["email_text"],
        )
        if not ctx.cfg.pipeline.dry_run:
            ctx.send_email(payload)
        return {}

    builder = StateGraph(AgentState)
    builder.add_node("fetch_candidates", fetch_candidates_node)
    builder.add_node("process_interest", process_interest_node)
    builder.add_node("compose_email", compose_email_node)
    builder.add_node("send_email", send_email_node)

    builder.add_edge(START, "fetch_candidates")
    builder.add_conditional_edges("fetch_candidates", fanout_interests, ["process_interest"])
    builder.add_edge("process_interest", "compose_email")
    builder.add_edge("compose_email", "send_email")
    builder.add_edge("send_email", END)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return builder.compile(checkpointer=checkpointer)
