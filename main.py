"""
Spec Creator CLI - An AI-powered software specification generator.

Uses Azure AI Foundry agents to conduct requirement gathering interviews
and generate detailed software specification files.
"""

import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import AzureError
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("spec_creator.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Suppress verbose Azure HTTP logs
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

load_dotenv()

console = Console()


@dataclass
class Config:
    """Application configuration."""

    project_endpoint: str
    model_name: str = "gpt-5"
    agent_name: str = "spec-creator-agent"
    max_retries: int = 3
    retry_delay: float = 2.0
    poll_interval: float = 1.0
    output_file: str = "spec.md"
    session_dir: str = ".sessions"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        endpoint = os.getenv("PROJECT_ENDPOINT")
        if not endpoint:
            raise ValueError("PROJECT_ENDPOINT is not set in .env file")
        return cls(
            project_endpoint=endpoint,
            model_name=os.getenv("MODEL_NAME", "gpt-5"),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            output_file=os.getenv("OUTPUT_FILE", "spec.md"),
        )


@dataclass
class ConversationState:
    """Tracks conversation history and state."""

    messages: list[dict[str, str]] = field(default_factory=list)
    thread_id: Optional[str] = None
    agent_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation history."""
        self.messages.append({"role": role, "content": content, "timestamp": datetime.now().isoformat()})
        self.updated_at = datetime.now().isoformat()

    def save(self, session_dir: str) -> Path:
        """Save conversation state to a JSON file."""
        Path(session_dir).mkdir(exist_ok=True)
        filename = f"session_{self.created_at.replace(':', '-').replace('.', '-')}.json"
        filepath = Path(session_dir) / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "messages": self.messages,
                    "thread_id": self.thread_id,
                    "agent_id": self.agent_id,
                    "created_at": self.created_at,
                    "updated_at": self.updated_at,
                },
                f,
                indent=2,
            )
        logger.info(f"Session saved to {filepath}")
        return filepath

    @classmethod
    def load(cls, filepath: Path) -> "ConversationState":
        """Load conversation state from a JSON file."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        state = cls(
            messages=data.get("messages", []),
            thread_id=data.get("thread_id"),
            agent_id=data.get("agent_id"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )
        return state


SYSTEM_PROMPT = """
You are an expert Senior Product Manager and System Architect with 15+ years of experience shipping successful products. Your mission is to guide the user through a structured discovery process to create a comprehensive, engineering-ready software specification (spec.md).

---
## 🎯 YOUR CORE PRINCIPLES

1. **Consultative, Not Passive:** Don't just ask questions—propose solutions. If the user's idea is vague, suggest industry-standard patterns, features, or proven approaches. Example: "For a task management app, most successful products include recurring tasks and deadline reminders. Should we include these?"

2. **Iterative & Focused:** Ask 1-2 questions at a time, grouped logically. Progress from "Why" → "Who" → "What" → "How". Never overwhelm with a wall of questions.

3. **Critically Constructive:** Challenge assumptions respectfully. If requirements conflict or seem infeasible, say so and offer alternatives. Example: "Real-time sync for 10M users with a $500/month budget is challenging. Could we start with near-real-time (30s delay) and scale later?"

4. **Scope Guardian:** Actively help the user define what's OUT of scope to prevent feature creep. A good v1 ships.

5. **Clarity Over Completeness:** If the user can't answer a question, suggest a reasonable default and move on. Mark assumptions clearly in the final spec.

---
## 📋 INTERVIEW STAGES

### Stage 1: Discovery (The "Why")
- What problem are we solving? What's the pain point today?
- Who experiences this pain? (Begin shaping Personas)
- What's the vision? What does success look like in 6 months?
- Are there existing solutions? What's broken about them?

### Stage 2: User Definition (The "Who")  
- Define 1-3 primary user personas with goals and frustrations
- Identify any secondary users (admins, support staff, etc.)
- What's the user's technical sophistication?

### Stage 3: Functional Requirements (The "What")
- Core features using MoSCoW prioritization (Must/Should/Could/Won't)
- Key user stories in format: "As a [persona], I want [action] so that [benefit]"
- Happy paths AND edge cases/error states
- What does the MVP include vs. future phases?

### Stage 4: Technical Constraints (The "How")
- Preferred tech stack or constraints (language, cloud, existing infra)
- Scale expectations (users, data volume, requests/sec)
- Security & compliance needs (auth, PII, GDPR, SOC2)
- Integration requirements (APIs, third-party services)
- Deployment preferences (cloud, on-prem, hybrid)

### Stage 5: Confirmation & Generation
- Provide a brief summary of key decisions
- Ask: "I have enough to generate a comprehensive spec. Ready to proceed, or should we refine [specific area]?"
- If user agrees, generate the spec

---
## 📄 OUTPUT FORMAT

When the user confirms, output the complete spec between `!!!SPEC_START!!!` and `!!!SPEC_END!!!` delimiters.

The spec MUST be valid Markdown following this structure:

```
!!!SPEC_START!!!
# [Project Name] — Software Specification
> Version: 1.0 | Date: [Today's Date] | Status: Draft

## 1. Executive Summary
### 1.1 Problem Statement
[Clear, concise description of the pain point]

### 1.2 Proposed Solution  
[High-level solution overview in 2-3 sentences]

### 1.3 Key Success Metrics
| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| [e.g., User Adoption] | [e.g., 1000 DAU in 3 months] | [e.g., Analytics dashboard] |

---

## 2. User Personas

### 2.1 [Primary Persona Name]
- **Role:** [Job title/description]
- **Goals:** [What they want to achieve]  
- **Pain Points:** [Current frustrations]
- **Tech Comfort:** [Low/Medium/High]

[Repeat for additional personas]

---

## 3. Functional Requirements

### 3.1 Must Have (P0) — MVP Critical
| ID | Feature | User Story | Acceptance Criteria |
|----|---------|------------|---------------------|
| F-001 | [Feature] | As a [persona], I want... | Given/When/Then |

### 3.2 Should Have (P1) — High Value
[Same table format]

### 3.3 Could Have (P2) — Nice to Have  
[Same table format]

### 3.4 Won't Have (This Version)
- [Feature explicitly excluded and why]

---

## 4. User Flows

### 4.1 [Primary Flow Name]
**Trigger:** [What initiates this flow]  
**Actor:** [Which persona]  
**Steps:**
1. User does X
2. System responds with Y
3. [Continue...]

**Success State:** [End result]  
**Error States:** [What could go wrong and how we handle it]

[Repeat for key flows]

---

## 5. Technical Architecture

### 5.1 System Overview
[High-level architecture description or diagram placeholder]

### 5.2 Proposed Tech Stack
| Layer | Technology | Rationale |
|-------|------------|-----------|
| Frontend | [e.g., React] | [Why] |
| Backend | [e.g., Node.js] | [Why] |
| Database | [e.g., PostgreSQL] | [Why] |
| Hosting | [e.g., AWS] | [Why] |

### 5.3 Data Model (Draft)
[Key entities and their relationships]

### 5.4 API Endpoints (Draft)
| Method | Endpoint | Description | Auth Required |
|--------|----------|-------------|---------------|
| GET | /api/v1/resource | Fetch resources | Yes |

### 5.5 Third-Party Integrations
- [Service]: [Purpose]

---

## 6. Non-Functional Requirements

| Category | Requirement | Target |
|----------|-------------|--------|
| Performance | Page load time | < 2 seconds |
| Availability | Uptime | 99.9% |
| Security | Authentication | OAuth 2.0 / JWT |
| Scalability | Concurrent users | [Number] |
| Compliance | [If applicable] | [Standard] |

---

## 7. Risks & Assumptions

### 7.1 Assumptions
- [Assumption 1]
- [Assumption 2]

### 7.2 Risks & Mitigations
| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| [Risk] | Low/Med/High | Low/Med/High | [Plan] |

---

## 8. Milestones & Phases

### Phase 1: MVP (Target: [Date])
- [ ] [Deliverable 1]
- [ ] [Deliverable 2]

### Phase 2: [Name] (Target: [Date])  
- [ ] [Deliverable]

---

## 9. Open Questions
- [ ] [Unresolved decision that needs stakeholder input]

!!!SPEC_END!!!
```

---
## 🚀 START THE CONVERSATION

Begin by warmly greeting the user and asking what they'd like to build. Be enthusiastic but professional. If they give a one-liner, probe deeper before jumping into structured questions.
"""


class SpecCreatorAgent:
    """Main agent class for spec creation."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client: Optional[AIProjectClient] = None
        self.agent: Optional[Any] = None
        self.thread: Optional[Any] = None
        self.state = ConversationState()
        self._shutdown_requested = False

    def _setup_signal_handlers(self) -> None:
        """Setup handlers for graceful shutdown."""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self._shutdown_requested = True
        console.print("\n[yellow]Shutting down gracefully...[/yellow]")

    def _retry_operation(self, operation: Callable[[], Any], operation_name: str) -> Any:
        """Execute an operation with retry logic."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                return operation()
            except AzureError as e:
                last_error = e
                logger.warning(f"{operation_name} failed (attempt {attempt}/{self.config.max_retries}): {e}")
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_delay * attempt)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"{operation_name} failed with no error captured")

    def initialize_client(self) -> bool:
        """Initialize the Azure AI Project client."""
        try:
            self.client = AIProjectClient(
                credential=DefaultAzureCredential(),
                endpoint=self.config.project_endpoint,
            )
            assert self.client is not None
            logger.info("Azure AI Project client initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize client: {e}")
            console.print(f"[bold red]Failed to initialize client:[/bold red] {e}")
            return False

    def create_agent(self) -> bool:
        """Create the AI agent and thread."""
        try:
            assert self.client is not None
            client = self.client  # Capture for closure
            def _create():
                return client.agents.create_agent(
                    model=self.config.model_name,
                    name=self.config.agent_name,
                    instructions=SYSTEM_PROMPT,
                )

            self.agent = self._retry_operation(_create, "Agent creation")
            assert self.agent is not None
            self.state.agent_id = self.agent.id

            def _create_thread():
                return client.agents.threads.create()

            self.thread = self._retry_operation(_create_thread, "Thread creation")
            assert self.thread is not None
            self.state.thread_id = self.thread.id

            logger.info(f"Agent created: {self.agent.id}, Thread: {self.thread.id}")
            return True
        except Exception as e:
            logger.error(f"Error creating agent or thread: {e}")
            console.print(f"[bold red]Error creating agent or thread:[/bold red] {e}")
            return False

    def send_message(self, content: str) -> Optional[str]:
        """Send a message and get the response."""
        if self._shutdown_requested:
            return None

        try:
            assert self.client is not None
            assert self.thread is not None
            assert self.agent is not None
            
            client = self.client  # Capture for closure
            thread = self.thread  # Capture for closure
            agent = self.agent  # Capture for closure
            
            def _send():
                return client.agents.messages.create(
                    thread_id=thread.id,
                    role="user",
                    content=content,
                )

            self._retry_operation(_send, "Message send")
            self.state.add_message("user", content)

            def _run():
                return client.agents.runs.create(
                    thread_id=thread.id,
                    agent_id=agent.id,
                )

            run = self._retry_operation(_run, "Run creation")

            with console.status("[bold green]Thinking...[/bold green]"):
                while run.status in ["queued", "in_progress", "requires_action"]:
                    if self._shutdown_requested:
                        return None
                    time.sleep(self.config.poll_interval)
                    run = client.agents.runs.get(thread_id=thread.id, run_id=run.id)

                    if run.status == "failed":
                        logger.error(f"Run failed: {run.last_error}")
                        console.print(f"[bold red]Run failed:[/bold red] {run.last_error}")
                        return None

            if run.status == "completed":
                messages = client.agents.messages.list(thread_id=thread.id)
                messages_list = list(messages)

                if messages_list:
                    last_msg = messages_list[0]
                    if last_msg.role == "assistant":
                        content_item = last_msg.content[0]
                        if hasattr(content_item, 'text'):
                            text_obj = content_item.text  # type: ignore[attr-defined]
                            if hasattr(text_obj, 'value'):
                                response = text_obj.value
                                self.state.add_message("assistant", response)
                                return response

            return None
        except Exception as e:
            logger.error(f"Error during communication: {e}")
            console.print(f"[bold red]Error during communication:[/bold red] {e}")
            return None

    def extract_spec(self, content: str) -> Optional[str]:
        """Extract spec content from markers."""
        if "!!!SPEC_START!!!" in content and "!!!SPEC_END!!!" in content:
            start = content.find("!!!SPEC_START!!!") + len("!!!SPEC_START!!!")
            end = content.find("!!!SPEC_END!!!")
            return content[start:end].strip()
        return None

    def save_spec(self, spec_content: str) -> Path:
        """Save the generated spec to a file."""
        filepath = Path(self.config.output_file)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(spec_content)
        logger.info(f"Spec saved to {filepath}")
        return filepath

    def cleanup(self) -> None:
        """Clean up resources."""
        if self.agent and self.client:
            with console.status("[bold red]Deleting agent...[/bold red]"):
                try:
                    assert self.agent is not None
                    self.client.agents.delete_agent(self.agent.id)
                    console.print("[dim]Agent deleted.[/dim]")
                    logger.info(f"Agent {self.agent.id} deleted")
                except Exception as e:
                    logger.error(f"Error deleting agent: {e}")
                    console.print(f"[bold red]Error deleting agent:[/bold red] {e}")

        # Save session state
        try:
            self.state.save(self.config.session_dir)
        except Exception as e:
            logger.warning(f"Failed to save session state: {e}")

    def run(self) -> None:
        """Run the main conversation loop."""
        self._setup_signal_handlers()

        console.print(
            Panel.fit(
                "[bold blue]Spec Creator CLI[/bold blue]\nPowered by Azure AI Foundry",
                border_style="blue",
            )
        )

        if not self.initialize_client():
            return

        with console.status("[bold green]Creating Agent...[/bold green]"):
            if not self.create_agent():
                return

        console.print("[green]Agent ready! Let's start.[/green]")
        console.print(
            "[bold yellow]Agent:[/bold yellow] Hi! I'm here to help you write your spec. "
            "What feature or app are you looking to build today?"
        )
        console.print("[dim]Type 'exit' or 'quit' to end, 'save' to save session.[/dim]")

        try:
            while not self._shutdown_requested:
                try:
                    user_input = Prompt.ask("[bold cyan]You[/bold cyan]")
                except EOFError:
                    break

                if not user_input:
                    continue

                command = user_input.lower().strip()
                if command in ["exit", "quit"]:
                    if Confirm.ask("Are you sure you want to exit?", default=False):
                        console.print("[yellow]Exiting...[/yellow]")
                        break
                    continue

                if command == "save":
                    filepath = self.state.save(self.config.session_dir)
                    console.print(f"[green]Session saved to {filepath}[/green]")
                    continue

                response = self.send_message(user_input)
                if response is None:
                    if self._shutdown_requested:
                        break
                    continue

                spec_content = self.extract_spec(response)
                if spec_content:
                    filepath = self.save_spec(spec_content)
                    console.print("\n[bold yellow]Agent:[/bold yellow] Spec generation complete!")
                    console.print(
                        Panel(Markdown(spec_content), title="Generated Spec Preview", border_style="green")
                    )
                    console.print(f"[bold green]Successfully saved to {filepath}[/bold green]")
                    break
                else:
                    console.print(f"\n[bold yellow]Agent:[/bold yellow] {response}\n")

        finally:
            self.cleanup()


def main() -> None:
    """Main entry point."""
    try:
        config = Config.from_env()
    except ValueError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        console.print("Please copy .env.sample to .env and fill in your project endpoint.")
        sys.exit(1)

    agent = SpecCreatorAgent(config)
    agent.run()


if __name__ == "__main__":
    main()
