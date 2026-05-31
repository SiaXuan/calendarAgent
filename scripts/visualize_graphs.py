"""
Visualize the LangGraph pipelines.

Usage (from project root, with .venv active):
  .venv/bin/python scripts/visualize_graphs.py          # ASCII to stdout
  .venv/bin/python scripts/visualize_graphs.py mermaid  # mermaid markdown to stdout
  .venv/bin/python scripts/visualize_graphs.py png      # write .png files into docs/

The PNG path uses the mermaid.ink rendering service (network call).
"""
import sys
from pathlib import Path

# Allow running from anywhere — put project root on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from graphs.adjust_graph import build_adjust_graph  # noqa: E402
from graphs.schedule_graph import build_schedule_graph  # noqa: E402


GRAPHS = {
    "schedule": build_schedule_graph,
    "adjust": build_adjust_graph,
}


def render_ascii() -> None:
    for name, builder in GRAPHS.items():
        print(f"\n=== {name} ===\n")
        print(builder().get_graph().draw_ascii())


def render_mermaid() -> None:
    for name, builder in GRAPHS.items():
        print(f"\n## {name}\n```mermaid")
        print(builder().get_graph().draw_mermaid())
        print("```")


def render_png(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, builder in GRAPHS.items():
        png_bytes = builder().get_graph().draw_mermaid_png()
        path = out_dir / f"{name}_graph.png"
        path.write_bytes(png_bytes)
        print(f"wrote {path}")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "ascii"
    if mode == "ascii":
        render_ascii()
    elif mode == "mermaid":
        render_mermaid()
    elif mode == "png":
        render_png(Path(__file__).parent.parent / "docs")
    else:
        print(f"Unknown mode: {mode}. Use ascii | mermaid | png.")
        sys.exit(1)


if __name__ == "__main__":
    main()
