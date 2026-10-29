from dataclasses import dataclass
from typing import Literal


EmployeeMode = Literal["analysis", "development"]


@dataclass(frozen=True)
class LocalEmployeeProfile:
    employee_id: str
    agent_file: str
    mode: EmployeeMode

    @property
    def agent_name(self) -> str:
        return self.agent_file.removesuffix(".md")

    @property
    def source(self) -> str:
        return f"Codex SDK · ChatGPT · {self.agent_name}"


def profile(employee_id: str, agent_file: str, mode: EmployeeMode) -> LocalEmployeeProfile:
    return LocalEmployeeProfile(employee_id=employee_id, agent_file=agent_file, mode=mode)


LOCAL_EMPLOYEE_PROFILES = {
    item.employee_id: item
    for item in (
        profile("research-factcheck", "product-trend-researcher.md", "analysis"),
        profile("data-analyst", "academic-statistician.md", "analysis"),
        profile("tech-lead", "engineering-software-architect.md", "development"),
        profile("frontend", "engineering-frontend-developer.md", "development"),
        profile("backend-integration", "backend-architect-with-memory.md", "development"),
        profile("ai-automation", "engineering-ai-engineer.md", "development"),
        profile("quality-assurance", "testing-test-automation-engineer.md", "development"),
        profile("devops-security", "engineering-devops-automator.md", "development"),
        profile("ui-ux-designer", "design-ui-designer.md", "development"),
        profile("marketing-lead", "marketing-growth-hacker.md", "analysis"),
        profile("content-copy", "marketing-content-creator.md", "analysis"),
        profile("performance-analyst", "support-analytics-reporter.md", "analysis"),
        profile("customer-inquiry", "customer-service.md", "analysis"),
        profile("order-delivery", "customer-service.md", "analysis"),
        profile("returns-voc", "retail-customer-returns.md", "analysis"),
        profile("risk-analyst", "finance-investment-researcher.md", "analysis"),
    )
}
