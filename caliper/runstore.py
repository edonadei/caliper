"""Where a saved run lives, and the only module that knows.

A run is a JSON file at ``<root>/.caliper/results/<spec>/<run id>.json``, where
the run id is the run's UTC timestamp. Three facts follow from that layout — the
directory shape, the run-id format, and "latest is lexicographically last" — and
they used to be known independently by the writer and by each of the three
commands that read runs back. They drifted: the listing re-derived the
latest-run rule that run addressing already owned, and wrapped its parse in a
bare ``except Exception`` because nothing else validated the file.

They live here now. A command says what it wants (this spec's latest run, this
spec's runs, every spec) and never spells the layout itself.

The run id is the run's timestamp; see
docs/adr/0021-a-run-is-addressed-by-its-timestamp.md for what that buys and
costs.

**Every command resolves the same root, by discovering it.** ``run`` used to
root the store at the spec file's parent while the reading commands rooted
theirs at the working directory, so ``caliper run evals/my.eval.yaml`` filed a
run that ``caliper report my`` could not find. Both sides now call
:meth:`RunStore.discover`, which walks up from the working directory for the
nearest ``.caliper/``. See
docs/adr/0022-saved-runs-live-at-a-discovered-results-root.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from caliper.schema.results import RunResults

#: Caliper's own directory, and the marker that says "a results root is here".
CALIPER_DIR = ".caliper"

#: Where runs are filed within it.
RESULTS_DIR = "results"

#: The run id: a UTC timestamp that sorts lexicographically, so "latest" needs
#: no parsing and a directory listing is already in run order.
RUN_ID_FORMAT = "%Y-%m-%dT%H-%M-%SZ"


class UnreadableRun(Exception):
    """A results file exists but could not be read as a run.

    Named rather than swallowed: the listing renders one row per run file and has
    to survive a corrupt one, but a reader who sees "?" needs to be told *which*
    file to go and look at.
    """

    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"{path}: {cause}")
        self.path = path
        self.cause = cause


@dataclass(frozen=True)
class RunStore:
    """The saved runs under one root directory."""

    root: Path = Path(".")

    @classmethod
    def discover(cls, start: Path | None = None) -> RunStore:
        """The results root that governs ``start`` (default: the working directory).

        The nearest ``.caliper/`` at or above ``start`` wins, so a package inside
        a monorepo that has deliberately been given its own root keeps its own
        eval history rather than pooling it with the repo's. The walk stops at
        the enclosing git repository: caliper will not reach past a repo
        boundary to adopt a root belonging to some unrelated parent — a stray
        ``.caliper/`` in ``$HOME`` must not silently become every repo's store.

        When no root exists yet, one is *named* (not created — ``save`` does
        that) at the repository root, falling back to ``start`` outside a
        repository. Deliberately not ``start`` itself: the first run of a
        project would otherwise plant the root wherever the caller happened to
        be standing, and every later run from elsewhere in the same project
        would create a second one. Nothing about that is visible until a
        ``report`` comes up empty.

        The escape hatch is the marker itself: ``mkdir .caliper`` in a directory
        makes it a results root, and nearest-wins does the rest.
        """
        start = (start or Path.cwd()).resolve()
        repo_root: Path | None = None
        for directory in (start, *start.parents):
            if (directory / CALIPER_DIR).is_dir():
                return cls(directory)
            if (directory / ".git").exists():
                repo_root = directory
                break
        return cls(repo_root or start)

    @property
    def caliper_dir(self) -> Path:
        """Caliper's own directory under this root — everything it writes.

        The run sandbox forbids the agent-under-test from reading it: a skill
        that opens last run's results is reading the answer key.
        """
        return self.root / CALIPER_DIR

    @property
    def results_dir(self) -> Path:
        return self.caliper_dir / RESULTS_DIR

    def spec_dir(self, spec: str) -> Path:
        """Where this spec's runs are filed. May not exist yet."""
        return self.results_dir / spec

    def no_results(self, ref: str) -> str:
        """Why a reference did not resolve, as a message that names the root.

        Two failures reach a reader as the same empty answer, and they need
        different fixes: the spec name is wrong, or the command is being run
        somewhere other than the project. The second is the one a bare "no
        results for 'x'" hides — it reads as a typo. Both name the root that was
        searched, because a *discovered* root is the one thing the caller cannot
        see (docs/CONTEXT.md → Results root).
        """
        if not self.has_any_results():
            return (
                f"No evaluation results under {self.root}.\n\n"
                "Run `caliper run` first, or work from the directory you ran it in."
            )
        return f"No results found for {ref!r} under {self.root}"

    def has_any_results(self) -> bool:
        """Whether caliper has ever saved a run under this root.

        Separates two failures a reader hits with the same symptom: an unknown
        spec name, and a results root that has never been written to at all —
        which usually means the command was run somewhere other than the project
        (docs/CONTEXT.md → Results root).
        """
        return self.results_dir.is_dir()

    def has_spec(self, spec: str) -> bool:
        """Whether this spec has ever been run here, even if every run was deleted.

        Distinct from ``runs(spec)`` being empty: a spec caliper has never heard
        of is a typo to report, while one with an empty directory has simply had
        its runs cleared.
        """
        return self.spec_dir(spec).is_dir()

    # --- writing ----------------------------------------------------------

    def save(self, results: RunResults) -> Path:
        """Write a run and return its path.

        An interrupted run is saved like any other: ``RunMeta.interrupted`` is
        what says the sample is short, and nothing about the file's location or
        name marks it (see ``_save_and_report``).
        """
        out_dir = self.spec_dir(results.run.spec)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{self.run_id(results)}.json"
        out_file.write_text(results.model_dump_json(indent=2))
        return out_file

    @staticmethod
    def run_id(results: RunResults) -> str:
        """The stem a caller passes back to ``report --run`` or names in ``compare``."""
        return results.run.timestamp.strftime(RUN_ID_FORMAT)

    # --- reading ----------------------------------------------------------

    @staticmethod
    def load(path: Path) -> RunResults:
        """Read one saved run, or raise :class:`UnreadableRun` naming the file."""
        try:
            return RunResults.model_validate_json(path.read_text())
        except Exception as exc:
            raise UnreadableRun(path, exc) from exc

    def resolve(self, spec_or_file: str, run: str | None = None) -> Path | None:
        """Resolve a run reference to a path, or ``None`` when there is no such run.

        A reference is either a direct path to a results JSON, or a spec name —
        which means that spec's latest run unless ``run`` names one.
        """
        path = Path(spec_or_file)
        if path.suffix == ".json":
            return path if path.exists() else None

        if run:
            candidate = self.spec_dir(spec_or_file) / f"{run}.json"
            return candidate if candidate.exists() else None
        return self.latest(spec_or_file)

    def runs(self, spec: str) -> list[Path]:
        """This spec's runs, oldest first. Empty when the spec has never run."""
        return sorted(self.spec_dir(spec).glob("*.json"))

    def latest(self, spec: str) -> Path | None:
        """The most recent run of a spec, or ``None`` when it has none."""
        runs = self.runs(spec)
        return runs[-1] if runs else None

    def specs(self) -> list[str]:
        """Every spec that has at least one saved run, alphabetically.

        A spec directory left empty by a deleted run is not listed: the listing
        answers "what can I report on", and a name with nothing behind it is a
        row that leads nowhere.
        """
        if not self.results_dir.exists():
            return []
        return sorted(
            entry.name
            for entry in self.results_dir.iterdir()
            if entry.is_dir() and any(entry.glob("*.json"))
        )
