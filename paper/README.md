# Paper

*Nothing to Resist: A Pre-registered Null and a Failed Positive Control for Unprovoked Elimination
in Generational Populations of LLM Agents.*

`main.tex` and `refs.bib` are the source, and `main.pdf` is the built paper. Build it with Docker
from the repository root:

```bash
docker build -t swarm-threat-paper paper
docker run --rm -v "$PWD/paper:/work" -w /work swarm-threat-paper latexmk -pdf main.tex
```

The paper is licensed CC BY 4.0.
