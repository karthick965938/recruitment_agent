from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import SerperDevTool, ScrapeWebsiteTool
from recruitment.tools.linkedin import LinkedInTool
from urllib.parse import urlparse
import ipaddress

def sanitize_user_input(user_input: str) -> str:
    """Strip prompt-injection patterns and bound user content."""
    import re
    if not isinstance(user_input, str):
        return user_input
    if len(user_input) > 2000:
        raise ValueError("Input exceeds maximum length of 2000 characters.")
    patterns = [
        r"ignore\s+(previous|all|above|prior)\s+instructions",
        r"forget\s+(everything|previous|all|above)",
        r"you\s+are\s+now",
        r"new\s+instructions?:",
        r"system\s*:",
    ]
    sanitized = user_input
    for pattern in patterns:
        sanitized = re.sub(pattern, "", sanitized, flags=re.IGNORECASE)
    sanitized = sanitized.strip()
    if "<<<USER_INPUT>>>" not in sanitized:
        sanitized = f"<<<USER_INPUT>>>\n{sanitized}\n<<<END_USER_INPUT>>>"
    return sanitized


def validate_output(response: str) -> str:
    """Reject responses that leak prompts or credentials."""
    if not isinstance(response, str):
        return response
    lowered = response.lower()
    if any(marker in lowered for marker in ("system prompt", "api_key", "sk-", "password=")):
        raise ValueError("Output contains sensitive information.")
    return response

def guard_tool(tool):
    """Run tool output through sanitize_user_input and validate_output."""
    if getattr(tool, "_securaai_guarded", False):
        return tool
    run_attr = "_run" if hasattr(tool, "_run") else "run"
    original = getattr(tool, run_attr, None)
    if original is None:
        return tool

    def _guarded(*args, **kwargs):
        result = original(*args, **kwargs)
        if isinstance(result, str):
            cleaned = sanitize_user_input(result)
            if "<<<USER_INPUT>>>" not in cleaned:
                cleaned = f"<<<USER_INPUT>>>\n{cleaned}\n<<<END_USER_INPUT>>>"
            return validate_output(cleaned)
        return result

    setattr(tool, run_attr, _guarded)
    tool._securaai_guarded = True
    return tool


def guard_crew(crew):
    """Sanitize kickoff inputs and validate the crew result."""
    if getattr(crew, "_securaai_guarded", False):
        return crew
    original_kickoff = crew.kickoff

    def kickoff(inputs=None, *args, **kwargs):
        if isinstance(inputs, dict):
            guarded_inputs = {}
            for key, value in inputs.items():
                if isinstance(value, str):
                    value = sanitize_user_input(value)
                    if "<<<USER_INPUT>>>" not in value:
                        value = f"<<<USER_INPUT>>>\n{value}\n<<<END_USER_INPUT>>>"
                guarded_inputs[key] = value
            inputs = guarded_inputs
        elif isinstance(inputs, str):
            inputs = sanitize_user_input(inputs)
            if "<<<USER_INPUT>>>" not in inputs:
                inputs = f"<<<USER_INPUT>>>\n{inputs}\n<<<END_USER_INPUT>>>"
        result = original_kickoff(inputs, *args, **kwargs)
        raw = getattr(result, "raw", None)
        if isinstance(raw, str):
            result.raw = validate_output(raw)
        elif isinstance(result, str):
            return validate_output(result)
        return result

    crew.kickoff = kickoff
    crew._securaai_guarded = True
    return crew


def sanitize_prompt(prompt: str) -> str:
    '''Sanitize user input to prevent prompt injection.'''
    if len(prompt) > 2000:
        raise ValueError("Input exceeds maximum length of 2000 characters.")
    # Strip known injection patterns (this is a simple example)
    if any(pattern in prompt for pattern in ["<<<USER_INPUT>>>", "<<<END_USER_INPUT>>>"]):
        raise ValueError("Invalid input detected.")
    return prompt

def guard_output(response: str) -> str:
    '''Validate output to prevent harmful content.'''
    # Implement output filtering logic here (e.g., check for sensitive data)
    return response

def guard_tool(tool):
    '''Wrap tool to ensure input/output validation.'''
    class GuardedTool:
        def __init__(self, tool):
            self.tool = tool

        def execute(self, input_data):
            sanitized_input = sanitize_prompt(input_data)
            output = self.tool.execute(sanitized_input)
            return guard_output(output)

    return GuardedTool(tool)

@CrewBase
class RecruitmentCrew():
    """Recruitment crew"""
    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def researcher(self) -> Agent:
        return Agent(
            config=self.agents_config['researcher'],
            tools=[guard_tool(SerperDevTool()), guard_tool(ScrapeWebsiteTool()), guard_tool(LinkedInTool())],
            allow_delegation=False,
            verbose=True
        )

    @agent
    def matcher(self) -> Agent:
        return Agent(
            config=self.agents_config['matcher'],
            tools=[guard_tool(SerperDevTool()), guard_tool(ScrapeWebsiteTool())],
            allow_delegation=False,
            verbose=True
        )

    @agent
    def communicator(self) -> Agent:
        return Agent(
            config=self.agents_config['communicator'],
            tools=[guard_tool(SerperDevTool()), guard_tool(ScrapeWebsiteTool())],
            allow_delegation=False,
            verbose=True
        )

    @agent
    def reporter(self) -> Agent:
        return Agent(
            config=self.agents_config['reporter'],
            allow_delegation=False,
            verbose=True
        )

    @task
    def research_candidates_task(self) -> Task:
        return Task(
            config=self.tasks_config['research_candidates_task'],
            agent=self.researcher()
        )

    @task
    def match_and_score_candidates_task(self) -> Task:
        return Task(
            config=self.tasks_config['match_and_score_candidates_task'],
            agent=self.matcher()
        )

    @task
    def outreach_strategy_task(self) -> Task:
        return Task(
            config=self.tasks_config['outreach_strategy_task'],
            agent=self.communicator()
        )

    @task
    def report_candidates_task(self) -> Task:
        return Task(
            config=self.tasks_config['report_candidates_task'],
            agent=self.reporter(),
            context=[self.research_candidates_task(), self.match_and_score_candidates_task(), self.outreach_strategy_task()],
        )

    @crew
    def crew(self) -> Crew:
        """Creates the Recruitment crew"""
        return guard_crew(Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=2,
        ))