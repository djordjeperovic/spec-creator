import os
import sys
import time
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

# Load environment variables
load_dotenv()

console = Console()

PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")

if not PROJECT_ENDPOINT:
    console.print("[bold red]Error:[/bold red] PROJECT_ENDPOINT is not set in .env file.")
    console.print("Please copy .env.sample to .env and fill in your project endpoint.")
    sys.exit(1)

def main():
    console.print(Panel.fit("[bold blue]Spec Creator CLI[/bold blue]\nPowered by Azure AI Foundry", border_style="blue"))

    try:
        # Initialize Azure AI Project Client
        project_client = AIProjectClient(
            credential=DefaultAzureCredential(),
            endpoint=PROJECT_ENDPOINT,
        )
    except Exception as e:
        console.print(f"[bold red]Failed to initialize client:[/bold red] {e}")
        return

    # System prompt for the agent
    system_prompt = """
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

    with console.status("[bold green]Creating Agent...[/bold green]"):
        try:
            agent = project_client.agents.create_agent(
                model="gpt-5", 
                name="spec-creator-agent",
                instructions=system_prompt,
            )
            thread = project_client.agents.threads.create()
            # console.print(f"[dim]Thread ID: {thread.id}[/dim]")
        except Exception as e:
            console.print(f"[bold red]Error creating agent or thread:[/bold red] {e}")
            return

    console.print("[green]Agent ready! Let's start.[/green]")
    
    # Initial greeting from the agent doesn't happen automatically in this SDK flow usually unless we trigger it,
    # but the prompt says "Start by asking...". 
    # So we can kick off by asking the user to start.
    
    console.print("[bold yellow]Agent:[/bold yellow] Hi! I'm here to help you write your spec. What feature or app are you looking to build today?")

    while True:
        user_input = Prompt.ask("[bold cyan]You[/bold cyan]")
        
        if user_input.lower() in ['exit', 'quit']:
            console.print("[yellow]Exiting...[/yellow]")
            break

        # Send message to thread
        # Send message to thread
        # Send message to thread
        try:
            # console.print("[dim]Sending message...[/dim]")
            message = project_client.agents.messages.create(
                thread_id=thread.id,
                role="user",
                content=user_input,
            )
            # console.print("[dim]Message sent.[/dim]")

            # Run the agent
            # console.print("[dim]Creating run...[/dim]")
            run = project_client.agents.runs.create(
                thread_id=thread.id,
                agent_id=agent.id,
            )
            # console.print("[dim]Run created.[/dim]")

            # Poll for completion
            with console.status("[bold green]Thinking...[/bold green]"):
                while run.status in ["queued", "in_progress", "requires_action"]:
                    time.sleep(1)
                    run = project_client.agents.runs.get(thread_id=thread.id, run_id=run.id)
                    
                    if run.status == "failed":
                        console.print(f"[bold red]Run failed:[/bold red] {run.last_error}")
                        break

            if run.status == "completed":
                # Get messages
                messages = project_client.agents.messages.list(thread_id=thread.id)
                
                # Iterate to get data
                messages_list = list(messages)
                
                if messages_list:
                    last_msg = messages_list[0]
                    if last_msg.role == "assistant":
                        text_content = last_msg.content[0].text.value
                    
                    # Check for SPEC markers
                    if "!!!SPEC_START!!!" in text_content and "!!!SPEC_END!!!" in text_content:
                        # Extract spec
                        start_index = text_content.find("!!!SPEC_START!!!") + len("!!!SPEC_START!!!")
                        end_index = text_content.find("!!!SPEC_END!!!")
                        spec_content = text_content[start_index:end_index].strip()
                        
                        # Save to file
                        with open("spec.md", "w", encoding="utf-8") as f:
                            f.write(spec_content)
                        
                        console.print("\n[bold yellow]Agent:[/bold yellow] Spec generation complete!")
                        console.print(Panel(Markdown(spec_content), title="Generated Spec Preview", border_style="green"))
                        console.print("[bold green]Successfully saved to spec.md[/bold green]")
                        break
                    else:
                        console.print(f"\n[bold yellow]Agent:[/bold yellow] {text_content}\n")

        except Exception as e:
            console.print(f"[bold red]Error during communication:[/bold red] {e}")
            # import traceback
            # traceback.print_exc()

    # Cleanup
    with console.status("[bold red]Deleting agent...[/bold red]"):
        try:
            project_client.agents.delete_agent(agent.id)
            console.print("[dim]Agent deleted.[/dim]")
        except Exception as e:
            console.print(f"[bold red]Error deleting agent:[/bold red] {e}")

if __name__ == "__main__":
    main()
