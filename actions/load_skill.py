"""On-demand loader for local Agent Skills."""

from core.skill_loader import list_skills, load_skill


def load_skill_action(parameters: dict, **_context) -> str:
    p = parameters or {}
    operation = str(p.get("action") or "load").strip().lower()
    if operation == "list":
        skills = list_skills()
        if not skills:
            return "No valid local skills are installed."
        return "Available skills:\n" + "\n".join(
            f"- {item['name']}: {item['description']}" for item in skills
        )
    return load_skill(str(p.get("name") or ""))


TOOL = {
    "name": "load_skill",
    "description": (
        "Loads the full instructions for a relevant installed JARVIS skill. "
        "Use a skill only when its description matches the user's task."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "load (default) or list"},
            "name": {"type": "STRING", "description": "Exact skill name to load"},
        },
    },
    "handler": load_skill_action,
}
