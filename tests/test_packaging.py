"""What gets shipped, and that it says the right thing about itself."""
import tomllib
import unittest
from importlib.metadata import version
from pathlib import Path

import stageflow_mcp

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_the_version_is_the_installed_one(self):
        """Read rather than written down: a number kept in two places is a
        number that will disagree with itself, and the one an MCP client shows
        would be the copy nobody remembered to change."""
        self.assertEqual(stageflow_mcp.__version__, version("stageflow-mcp"))

    def test_the_installed_version_is_the_declared_one(self):
        with open(ROOT / "pyproject.toml", "rb") as handle:
            declared = tomllib.load(handle)["project"]["version"]
        self.assertEqual(version("stageflow-mcp"), declared)

    def test_the_console_script_is_the_cli(self):
        """`stageflow-mcp` on the PATH is the whole installation story; an entry
        point that moved would be discovered by a person, not by us."""
        with open(ROOT / "pyproject.toml", "rb") as handle:
            scripts = tomllib.load(handle)["project"]["scripts"]
        self.assertEqual(scripts["stageflow-mcp"], "stageflow_mcp.cli:main")

    def test_the_release_workflow_checks_the_tag(self):
        """The tag and project.version have to agree, and the workflow is what
        enforces it — a release named after the wrong version cannot be undone
        on PyPI."""
        workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
        self.assertIn("project\"][\"version\"]", workflow)
        self.assertIn("GITHUB_REF_NAME", workflow)


if __name__ == "__main__":
    unittest.main()
