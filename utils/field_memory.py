import json
import os

FIELD_MEMORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "field_memory.json"
)


def load_field_memory() -> dict:
    if not os.path.exists(FIELD_MEMORY_PATH):
        return {}
    with open(FIELD_MEMORY_PATH, "r") as f:
        return json.load(f)


def save_field_memory_answer(question: str, answer: str):
    memory = load_field_memory()
    memory[question] = answer
    os.makedirs(os.path.dirname(FIELD_MEMORY_PATH), exist_ok=True)
    with open(FIELD_MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)


def delete_field_memory_answer(question: str):
    memory = load_field_memory()
    memory.pop(question, None)
    with open(FIELD_MEMORY_PATH, "w") as f:
        json.dump(memory, f, indent=2)