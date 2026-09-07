"""Validate AI patches before they can reach the editor."""


def validate_change(change):
    if not isinstance(change, dict) or not change:
        raise ValueError("The AI returned an empty plan change")
    allowed = {"titles", "thumbnail_ideas", "intro_hook", "outline", "tags", "description", "links"}
    if set(change) - allowed:
        raise ValueError("The AI returned unsupported plan fields")
    for key, value in change.items():
        if key in {"intro_hook", "description"}:
            valid = isinstance(value, str)
        elif key in {"titles", "thumbnail_ideas", "tags"}:
            valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
            if key == "titles":
                valid = valid and bool(value) and all(v.strip() for v in value)
        elif key == "outline":
            valid = isinstance(value, list) and all(
                isinstance(v, dict) and isinstance(v.get("section"), str)
                and isinstance(v.get("points", []), list)
                and all(isinstance(point, str) for point in v.get("points", []))
                and all(isinstance(v.get(k, ""), str) for k in ("section_script", "duration_hint"))
                for v in value)
        else:
            valid = isinstance(value, list) and all(
                isinstance(v, dict) and isinstance(v.get("label"), str)
                and isinstance(v.get("url"), str) for v in value)
        if not valid:
            raise ValueError(f"The AI returned invalid {key}")
    return change
