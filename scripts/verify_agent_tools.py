"""
Audit and verify that all 22 Agents + Director have their tools properly registered and mapped.
"""
import sys
import os

# Add root to pythonpath
sys.path.insert(0, os.path.abspath("."))

from app.tools.tool_registry import tool_registry
from app.api.endpoints import agent_registry

def audit_agents_and_tools():
    all_agents = agent_registry.list_all_metadata()
    print(f"\n{'='*70}")
    print(f"NAPSTERTEC AI OS - AGENT & TOOL INTEGRITY AUDIT")
    print(f"Total Registered Agents: {len(all_agents)}")
    print(f"Total Registered Tools: {len(tool_registry._tools)}")
    print(f"{'='*70}\n")

    departments = {}
    missing_tools_total = 0

    for agent_meta in all_agents:
        dept = agent_meta.department_name or "Unassigned"
        if dept not in departments:
            departments[dept] = []
        departments[dept].append(agent_meta)

    for dept_name, agents in departments.items():
        print(f"\n📂 DEPARTMENT: {dept_name} ({len(agents)} agents)")
        print("-" * 60)
        for a in agents:
            allowed = a.allowed_tools
            missing = []
            for t in allowed:
                if t == "*":
                    continue
                try:
                    tool_registry.get_tool(t)
                except KeyError:
                    missing.append(t)

            status_icon = "✅" if not missing else "❌"
            print(f"  {status_icon} [{a.name}] {a.display_name}")
            print(f"     Allowed Tools ({len(allowed)}): {', '.join(sorted(allowed))}")
            if missing:
                print(f"     ⚠️ MISSING TOOLS IN REGISTRY: {', '.join(missing)}")
                missing_tools_total += len(missing)

    print(f"\n{'='*70}")
    if missing_tools_total == 0:
        print("🎉 ALL AGENTS HAVE 100% OF THEIR TOOLS REGISTERED AND OPERATIONAL!")
    else:
        print(f"⚠️ FOUND {missing_tools_total} UNREGISTERED TOOLS REQUIRED BY AGENTS.")
    print(f"{'='*70}\n")
    return missing_tools_total

if __name__ == "__main__":
    missing = audit_agents_and_tools()
    sys.exit(1 if missing > 0 else 0)
