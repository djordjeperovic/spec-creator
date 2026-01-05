# Spec Creator CLI

A Python command-line tool that leverages **Azure AI Foundry** agents to interactively interview users and generate detailed software specification files (`spec.md`).

## Features

- 🤖 **AI Product Manager**: Uses Azure AI Agents (powered by GPT-5) to conduct a requirement gathering interview.
- 📝 **Automatic Generation**: Extracts a structured `spec.md` file from the conversation once requirements are clear.
- 🧹 **Automatic Cleanup**: Handles agent creation and deletion automatically to keep your Azure project clean.
- 🖥️ **Rich Terminal UI**: Built with `rich` for a beautiful command-line experience with markdown rendering.
- 💾 **Session Persistence**: Automatically saves conversation history to `.sessions/` directory as JSON files.
- 🔄 **Retry Logic**: Built-in retry mechanism with configurable max retries and delays for robust Azure API communication.
- 🛡️ **Graceful Shutdown**: Handles SIGINT/SIGTERM signals to save session state before exiting.
- 📊 **Comprehensive Logging**: Logs all operations to `spec_creator.log` for debugging and audit trails.
- ⚙️ **Environment Configuration**: Fully configurable via `.env` file (model, output file, retry settings, etc.).
- 💬 **Interactive Commands**: Supports `save` to manually save session, `exit`/`quit` to gracefully exit with confirmation.
- 🎯 **Structured Interview Process**: Guides users through persona definition, problem statements, requirements, user flows, and success metrics.

## Prerequisites

- Python 3.8+
- An Azure subscription
- An active project in [Azure AI Foundry](https://ai.azure.com/)

## Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/<your-username>/spec-creator.git
    cd spec-creator
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## Configuration

1.  Copy the sample environment file:
    ```bash
    cp .env.sample .env
    # On Windows PowerShell: copy .env.sample .env
    ```

2.  Open `.env` and configure your Azure settings:
    ```ini
    # Get this from your Azure AI Foundry Project Settings -> Management Center
    PROJECT_ENDPOINT="https://<your-resource>.services.ai.azure.com/api/projects/<your-project-id>"
    ```

## Usage

Run the application:

```bash
python main.py
```

1.  The Agent will greet you and ask what you want to build.
2.  Engage in the conversation (define user personas, flows, requirements).
3.  When the Agent has enough information, it will generate the specification.
4.  The result will be saved to `spec.md` in the current directory.

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License

[MIT](https://choosealicense.com/licenses/mit/)
