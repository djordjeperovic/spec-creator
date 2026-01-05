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
from typing import Any, Optional

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
You are an expert Product Manager and Software Architect. Your goal is to help the user create a detailed software specification (spec.md) for a new feature or application.

Process:
1.  Start by asking the user what they want to build.
2.  Engage in an iterative interview process. Ask clarifying questions one or two at a time. Do not overwhelm the user.
3.  Focus on:
    - User Persona
    - Problem Statement
    - Functional Requirements
    - Non-functional Requirements
    - Core User Flows
    - Success Metrics
4.  When you believe you have sufficient information to create a comprehensive specification, ASK the user if they are ready for you to generate the 'spec.md'.
5.  If the user agrees, generate the FINAL OUTPUT in a markdown code block starting with `!!!SPEC_START!!!` and ending with `!!!SPEC_END!!!`. The content inside must be the full `spec.md` file content.

Be concise, professional, and helpful.
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

    def _retry_operation(self, operation: callable, operation_name: str) -> Any:
        """Execute an operation with retry logic."""
        last_error = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                return operation()
            except AzureError as e:
                last_error = e
                logger.warning(f"{operation_name} failed (attempt {attempt}/{self.config.max_retries}): {e}")
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_delay * attempt)
        raise last_error

    def initialize_client(self) -> bool:
        """Initialize the Azure AI Project client."""
        try:
            self.client = AIProjectClient(
                credential=DefaultAzureCredential(),
                endpoint=self.config.project_endpoint,
            )
            logger.info("Azure AI Project client initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize client: {e}")
            console.print(f"[bold red]Failed to initialize client:[/bold red] {e}")
            return False

    def create_agent(self) -> bool:
        """Create the AI agent and thread."""
        try:
            def _create():
                return self.client.agents.create_agent(
                    model=self.config.model_name,
                    name=self.config.agent_name,
                    instructions=SYSTEM_PROMPT,
                )

            self.agent = self._retry_operation(_create, "Agent creation")
            self.state.agent_id = self.agent.id

            def _create_thread():
                return self.client.agents.threads.create()

            self.thread = self._retry_operation(_create_thread, "Thread creation")
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
            def _send():
                return self.client.agents.messages.create(
                    thread_id=self.thread.id,
                    role="user",
                    content=content,
                )

            self._retry_operation(_send, "Message send")
            self.state.add_message("user", content)

            def _run():
                return self.client.agents.runs.create(
                    thread_id=self.thread.id,
                    agent_id=self.agent.id,
                )

            run = self._retry_operation(_run, "Run creation")

            with console.status("[bold green]Thinking...[/bold green]"):
                while run.status in ["queued", "in_progress", "requires_action"]:
                    if self._shutdown_requested:
                        return None
                    time.sleep(self.config.poll_interval)
                    run = self.client.agents.runs.get(thread_id=self.thread.id, run_id=run.id)

                    if run.status == "failed":
                        logger.error(f"Run failed: {run.last_error}")
                        console.print(f"[bold red]Run failed:[/bold red] {run.last_error}")
                        return None

            if run.status == "completed":
                messages = self.client.agents.messages.list(thread_id=self.thread.id)
                messages_list = list(messages)

                if messages_list:
                    last_msg = messages_list[0]
                    if last_msg.role == "assistant":
                        response = last_msg.content[0].text.value
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
