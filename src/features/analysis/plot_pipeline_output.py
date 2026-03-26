"""Plot ED/SQ and M1/M2 from ``pipeline_output.json`` in the project root."""

import json

import matplotlib.pyplot as plt

from src.utils.paths import project_root


def main() -> None:
    path = project_root() / "pipeline_output.json"
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    ed_scores = []
    sq_scores = []
    m1_scores = []
    m2_scores = []

    for item in data:
        ed = item.get("ed")
        sq = item.get("sq")
        m1 = item.get("m1")
        m2 = item.get("m2")

        if ed is not None and sq is not None:
            ed_scores.append(ed)
            sq_scores.append(sq)

        if m1 is not None and m2 is not None:
            m1_scores.append(m1)
            m2_scores.append(m2)

    plt.figure(figsize=(10, 4))

    plt.subplot(1, 2, 1)
    plt.scatter(ed_scores, sq_scores, color="steelblue")
    for i, (ed, sq) in enumerate(zip(ed_scores, sq_scores), start=1):
        plt.text(ed, sq, str(i))
    plt.xlabel("ED Score")
    plt.ylabel("SQ Score")
    plt.title("ED vs SQ")
    plt.grid(True)

    plt.subplot(1, 2, 2)
    plt.scatter(m1_scores, m2_scores, color="darkorange")
    for i, (m1, m2) in enumerate(zip(m1_scores, m2_scores), start=1):
        plt.text(m1, m2, str(i))
    plt.xlabel("M1 Score")
    plt.ylabel("M2 Score")
    plt.title("M1 vs M2")
    plt.grid(True)

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
