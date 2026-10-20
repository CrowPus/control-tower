"""Agents the engine tests work with. The core ships only Nia; an install's other agents are the owner's own (made
from the template in stage CT2). Until then the tests register these, shaped like the starter templates, so the
engine (runs, clearance, budgets, the studio, QA) is tested with agents that use its tools and actions."""

from __future__ import annotations

from app.services.team import registry
from app.services.team.registry import Agent

TEST_AGENTS = (
    Agent("marketing_lead", "Marketing Lead", "Marketing", "Plans the company's marketing: one campaign plan per period with briefs for the team.",
          tools=("social_results", "content_calendar", "marketing_plans", "brand_kit", "video_studio", "character"), actions=("marketing.plan",), max_rounds=16),
    Agent("content_community", "Social Media Manager", "Marketing", "Posts on the company's social networks after clearance and learns from the results.",
          subscribes=("marketing.brief", "video.ready"),
          tools=("brand_kit", "social_overview", "social_results", "content_calendar", "media_library", "create_image", "marketing_plans", "video_studio"),
          actions=("social.post",), max_rounds=14),
    Agent("graphic_designer", "Graphic Designer", "Marketing", "Video concepts with the company's character, made only after clearance.",
          subscribes=("marketing.brief",),
          tools=("character", "video_studio", "brand_kit", "marketing_plans", "social_results", "content_calendar", "web_search", "storyboard_frame",
                 "create_image", "media_library"),
          actions=("video.concept",), max_rounds=16),
    Agent("product_analyst", "Product Analyst", "Product", "Finds where customers get stuck and proposes one measured change at a time.",
          kpi="agent_runs", emits=("product.finding",), tools=("product_numbers", "product_ideas", "measure_idea"), actions=("product.idea",), max_rounds=12),
    Agent("qa_runner", "QA Runner", "Product", "Opens the owner's pages, reports what breaks and what is slow.",
          emits=("qa.failed",), tools=("run_checks", "qa_history")),
    Agent("engineer_on_duty", "Engineer on Duty", "Product", "Watches errors, uptime, tasks and mail; keeps an incident record.",
          kpi="server_errors", subscribes=("qa.failed", "qa.recovered", "alert.opened"), emits=("incident.note",),
          tools=("health_numbers", "ops_signals", "qa_history", "run_checks", "incidents", "open_incident", "update_incident"), max_rounds=12),
    Agent("trust_compliance", "Trust", "Trust", "Checks the data-deletion runs and proposals waiting too long for clearance.",
          emits=("compliance.issue",), tools=("read_web_page", "web_search")),
)


def register() -> None:
    for agent in TEST_AGENTS:
        registry.AGENTS.setdefault(agent.key, agent)
