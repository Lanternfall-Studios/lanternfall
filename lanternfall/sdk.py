"""Pure, inspectable helpers expanding to the same low-level actions for everyone.

Plans are not atomic or privileged: each action still consumes an actor's turn,
passes Engine.apply, and may fail due to changed world state.
"""
from collections import deque


def validate_action(action):
    """Bounded structural validation shared by HTTP queues and Engine.apply."""
    if not isinstance(action, (list, tuple)) or not action or len(action) > 8:
        raise ValueError("invalid action")
    verb, *args = action
    signatures = {"idle": (), "gather": (), "move": (str,), "craft": (str,),
                  "merchant_sell": (str,), "say": (str,), "cancel_stale": (int,),
                  "bid": (str, int, int), "ask": (str, int, int),
                  "respond": (int, bool),
                  "propose": (str, str, int, str, int, str)}
    # Gathering an explicit primary/secondary item is also an engine primitive.
    signature = (str,) if verb == "gather" and args else signatures.get(verb) if isinstance(verb, str) else None
    if signature is None or len(args) != len(signature):
        raise ValueError("invalid action")
    for value, expected in zip(args, signature):
        if type(value) is not expected:
            raise ValueError("invalid action argument")
        if expected is str and (not value or len(value) > 500 or any(ord(c) < 32 for c in value)):
            raise ValueError("invalid action text")
        if expected is int and not 0 <= value <= 1_000_000:
            raise ValueError("invalid action integer")
    if verb in {"bid", "ask"} and args[2] == 0:
        raise ValueError("quantity must be positive")
    if verb == "propose" and (args[2] == 0 or args[4] == 0):
        raise ValueError("quantity must be positive")
    return tuple(action)


def path_between(view, destination):
    start = view["me"]["zone"]
    frontier = deque([(start, [])])
    visited = {start}
    while frontier:
        zone, path = frontier.popleft()
        if zone == destination:
            return path
        for neighbor in sorted(view["zones"][zone]["links"]):
            if neighbor not in visited:
                visited.add(neighbor)
                frontier.append((neighbor, path + [("move", neighbor)]))
    raise ValueError("no route")


def path_to_resource(view, item):
    candidates = []
    for zone, node in view["nodes"].items():
        if node["item"] == item or (node.get("secondary") or {}).get("item") == item:
            path = path_between(view, zone)
            candidates.append((len(path), zone, path))
    if not candidates:
        raise ValueError("unknown resource")
    return min(candidates)[2] + [("gather", item)]


def propose_safe_trade(view, target, give, give_qty, want, want_qty):
    action = validate_action(("propose", target, give, give_qty, want, want_qty,
                              f"{give_qty}x {give}"))
    if give not in view["items"] or want not in view["items"]:
        raise ValueError("unknown item")
    if view["me"]["inventory"].get(give, 0) < give_qty:
        raise ValueError("insufficient offered items")
    return action  # actual terms visible; counterparty consent still required


def submit_work_order(target, directive):
    if target not in {"steward", "engineer"}:
        raise ValueError("unknown work recipient")
    return validate_action(("say", f"@{target} {directive}"))


def audit_commons_health(view):
    node = view["nodes"][view["commons_zone"]]
    return {"stock": node["stock"], "cap": node["cap"],
            "status": "depleted" if node["stock"] == 0 else
                      "low" if node["stock"] * 4 < node["cap"] else "healthy"}
