import anyio
from dotenv import load_dotenv
from claude_agent_sdk import query, ClaudeAgentOptions

load_dotenv()

options = ClaudeAgentOptions(
    cwd=r"C:\Dev V2\gtm-deep-agent",
    setting_sources=["project"],          # only THIS project's CLAUDE.md — no global plugins/hooks
    model="claude-sonnet-4-6",            # fast + cheap; easy to swap later
    allowed_tools=["Read", "Glob", "Grep"],
)

async def main():
    async for message in query(
        prompt="Why does pipe create have no CloseDate filter? Cite the doc.",
        options=options,
    ):
        print(message)

anyio.run(main)