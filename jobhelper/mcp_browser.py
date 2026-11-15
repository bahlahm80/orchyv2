import asyncio
import json
import logging
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from jobhelper.llm import generate_decision_with_fallback

logger = logging.getLogger("jobhelper.mcp_browser")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)

class MCPApplicationRunner:
    """Orchestrates job application workflows using the Playwright MCP Server."""

    async def run_application(self, target_url: str, job_title: str, company_name: str, candidate_profile: dict):
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", 
            "@playwright/mcp@latest",
            "--isolated"]
        )

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # List available tools from the Playwright MCP server
                tools = await session.list_tools()
                logger.info(f"[MCP] Initialized Playwright MCP Server with tools: {[t.name for t in tools.tools]}")

                # 1. Navigate to target URL
                logger.info(f"[MCP] Navigating to {target_url}")
                await session.call_tool("browser_navigate", arguments={"url": target_url})

                max_steps = 12
                for step in range(1, max_steps + 1):
                    await asyncio.sleep(3) # Rate limit delay

                    # Grab accessibility / page state snapshot from MCP tool
                    snapshot = await session.call_tool("browser_snapshot", arguments={})
                    snapshot_text = str(snapshot.content[0].text) if snapshot.content else ""

                    prompt = f"""
                    You are an autonomous browser agent executing a job application via MCP tools.
                    Target Company: {company_name}
                    Target Job Title: {job_title}
                    Candidate Profile: {json.dumps(candidate_profile)}

                    Current Page MCP Accessibility Snapshot:
                    {snapshot_text[:3000]}

                    Available MCP Tools:
                    - browser_click(target: str)
                    - browser_type(target: str, text: str)
                    - browser_navigate(url: str)
                    - browser_snapshot()
                    - done()

                    CRITICAL RULE: Before clicking any navigation links or search bars, check the web page for any cookie consent banners, privacy overlays. If present, you MUST click "Accept" or "Agree" first.  Do not attempt to click page links while an overlay is active.

                    Goal:
                    1. Always open the company career website and do not use LinkedIn or any other job board websites.
                    2. CRITICAL: Dismiss any cookie banners/overlays/privacy agreement if present and you must (click Accept/Allow). DO NOT GO TO THE NEXT STEP IF THIS IS NOT DONE.
                    3. Navigate to career page where you can search for the job.  If none, take a look and figure out where to go.  For example, if location needs to be chosen first, click location.  Any US location is good but put North Carolina as higher priority then eastern US then central US and lastly western US.
                    4. Search for or select the job matching "{job_title}".
                    5. Click Apply and fill out candidate details (Name, Email, Phone, Resume).
                    6. Select "done" once finished or submitted.

                    Respond ONLY in valid JSON:
                    {{
                        "thought": "Reasoning about current state",
                        "tool": "browser_click" | "browser_type" | "browser_navigate" | "done",
                        "args": {{ ... arguments for the chosen tool ... }}
                    }}
                    """

                    decision = await generate_decision_with_fallback(prompt, system="You are an expert MCP agent controller.")
                    logger.info(f"[MCP Step {step}] Thought: {decision.get('thought')}")
                    logger.info(f"[MCP Step {step}] Chosen Tool: {decision.get('tool')} with args {decision.get('args')}")
                    logger.info(f"[MCP Step {step}] {decision.get('thought')}")

                    tool_name = decision.get("tool")
                    args = decision.get("args", {})

                    if tool_name == "done":
                        logger.info("[MCP Agent] Job application completed successfully.")
                        break

                   # Map any variant key ('element', 'ref', 'selector') to 'target' for Playwright MCP
                    if tool_name in ("browser_click", "browser_type"):
                        target_val = args.get("target") or args.get("element") or args.get("ref") or args.get("selector")
                        if target_val:
                            args["target"] = str(target_val)
                            # Remove non-standard keys so MCP receives a clean dict
                            for key in ["element", "ref", "selector"]:
                                args.pop(key, None)

                    # Execute the tool via MCP protocol
                    try:
                        res = await session.call_tool(tool_name, arguments=args)
                        res_text = str(res.content[0].text).replace("\n", " ").strip() if (res and res.content) else "OK"
                        logger.info(f"[MCP Tool Execution] Executed {tool_name}: {res_text[:120]}")
                    except Exception as err:
                        logger.error(f"[MCP Step {step}] Tool execution error: {err}")
                        await asyncio.sleep(5)