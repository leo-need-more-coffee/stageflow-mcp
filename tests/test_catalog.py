"""What the model is given to read: stages, capabilities, the schema, the guide."""
import unittest

from stageflow_mcp.backend import Backend
from stageflow_mcp.catalog import (
    EXAMPLES, GUIDE, capabilities, pipeline_schema, prose, stage_catalog,
)
from tests.fake import FakeBackend

MAPPING = {"en": "in English", "ru": "по-русски"}


class ProseTests(unittest.TestCase):
    """The editor's rule, for the opposite reason: one language, not all."""

    def test_the_asked_language_wins(self):
        self.assertEqual(prose(MAPPING, "ru"), "по-русски")

    def test_a_regional_tag_is_satisfied_by_its_language(self):
        self.assertEqual(prose(MAPPING, "ru-RU"), "по-русски")

    def test_a_language_nobody_translated_falls_back_to_english(self):
        self.assertEqual(prose(MAPPING, "fr"), "in English")

    def test_a_mapping_with_neither_gives_up_what_it_has(self):
        """A stage described in Polish and nothing else is better read in
        Polish than not at all."""
        self.assertEqual(prose({"pl": "po polsku"}, "ru"), "po polsku")

    def test_a_plain_string_is_prose_in_one_language(self):
        self.assertEqual(prose("as it came", "ru"), "as it came")


class CatalogTests(unittest.TestCase):
    def test_every_description_is_collapsed(self):
        with FakeBackend() as fake:
            stages = stage_catalog(Backend(fake.url), "ru")
            self.assertEqual(stages["SetValueStage"]["description"], "Кладёт значение")
            self.assertEqual(stages["SetValueStage"]["arguments"][0]["description"],
                             "что положить")
            self.assertEqual(stages["SetValueStage"]["outputs"][0]["description"],
                             "что положили")

    def test_an_untranslated_stage_survives_untouched(self):
        with FakeBackend() as fake:
            stages = stage_catalog(Backend(fake.url), "ru")
            self.assertEqual(stages["LonelyStage"]["description"],
                             "Only one language, so a plain string")

    def test_a_backend_without_meta_is_not_second_guessed(self):
        with FakeBackend() as fake:
            fake.script.serve_meta = False
            answer = capabilities(Backend(fake.url))
            self.assertFalse(answer["available"])
            self.assertIn("does not serve", answer["note"])


class SchemaTests(unittest.TestCase):
    def test_the_schema_is_narrowed_to_this_backend(self):
        """Both enums come from the live backend: a stage it does not serve and
        a node type it cannot run are refused by the shape, before semantics."""
        with FakeBackend() as fake:
            schema = pipeline_schema(Backend(fake.url))
            defs = schema["$defs"]
            self.assertEqual(defs["stage_node"]["properties"]["stage"]["enum"],
                             ["LonelyStage", "SetValueStage"])
            self.assertEqual(defs["node"]["properties"]["type"]["enum"],
                             ["condition", "entry", "stage", "terminal"])

    def test_without_meta_the_node_types_stay_open(self):
        """The shipped schema accepts any type string and lets the core say
        `Unknown node type`. With nothing to narrow by, that stands."""
        with FakeBackend() as fake:
            fake.script.serve_meta = False
            schema = pipeline_schema(Backend(fake.url))
            self.assertNotIn("enum", schema["$defs"]["node"]["properties"]["type"])


class GuideTests(unittest.TestCase):
    """The one thing here that is written rather than fetched, and so the one
    thing that can quietly become fiction."""

    def test_every_node_type_it_names_is_a_real_one(self):
        from stageflow.core.nodes import get_node_types

        registry = set(get_node_types())
        named = {"entry", "stage", "condition", "switch", "terminal",
                 "parallel", "try", "map", "subpipeline"}
        self.assertTrue(named <= registry, f"the guide names types that are gone: {named - registry}")

    def test_the_examples_only_use_node_types_that_exist(self):
        from stageflow.core.nodes import get_node_types

        registry = set(get_node_types())
        for name, graph in EXAMPLES.items():
            for node in graph["nodes"]:
                self.assertIn(node["type"], registry, f"{name}: {node['id']}")

    def test_the_examples_pass_the_schema(self):
        """Shipped as things to copy, so they had better be shaped right. This
        is the JSON Schema only — whether a backend will RUN them is that
        backend's answer, and `tests/test_live.py` asks for it."""
        import jsonschema
        from stageflow.docs import load_pipeline_schema

        schema = load_pipeline_schema()
        for name, graph in EXAMPLES.items():
            with self.subTest(example=name):
                jsonschema.validate(instance=graph, schema=schema)

    def test_the_examples_place_no_nodes(self):
        """`metadata.ui` is where a person dragged a card. A graph written from
        here has nobody to have dragged it, and inventing coordinates produces a
        pile — the editor lays out what arrives without them."""
        for name, graph in EXAMPLES.items():
            for node in graph["nodes"]:
                self.assertNotIn("ui", node.get("metadata", {}), f"{name}: {node['id']}")

    def test_the_guide_says_not_to_place_the_nodes(self):
        self.assertIn("metadata.ui", GUIDE)

    def test_the_guide_says_how_to_reach_a_variable(self):
        """The one CEL detail that is not guessable and is got wrong by
        default: a non-identifier name needs the bracket form."""
        self.assertIn("vars['имя']", GUIDE)


if __name__ == "__main__":
    unittest.main()
