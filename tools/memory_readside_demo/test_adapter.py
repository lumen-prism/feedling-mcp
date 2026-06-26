import unittest

from tools.memory_readside_demo.adapter import (
    InMemoryMemoryCore,
    MemoryCard,
    MemoryMoment,
    MemoryStatus,
    Salience,
    memory_moment_to_card,
    memory_moment_to_fetch_result,
    memory_moment_to_index_item,
)
from tools.memory_readside_demo.demo import (
    build_demo_payload,
    sample_insert_card,
    sample_legacy_memory_moment,
    sample_superseding_card,
)


class MemoryCoreDemoTests(unittest.TestCase):
    def test_memory_moment_to_index_item_excludes_verbatim(self):
        moment = sample_legacy_memory_moment()

        item = memory_moment_to_index_item(moment)

        self.assertEqual(item.memory_id, "mem_old_advice")
        self.assertEqual(item.summary, "用户不喜欢在难过时被立刻给建议。")
        self.assertEqual(item.bucket_refs, ["陪伴方式"])
        self.assertEqual(item.status, "active")
        self.assertFalse(hasattr(item, "verbatim"))

    def test_memory_moment_to_fetch_result_keeps_verbatim_and_context(self):
        moment = sample_legacy_memory_moment()

        result = memory_moment_to_fetch_result(moment)

        self.assertEqual(result.memory_id, "mem_old_advice")
        self.assertEqual(result.verbatim, "你每次一给步骤我就觉得你不在我身边。")
        self.assertEqual(result.context, "一次情绪崩溃的聊天")
        self.assertEqual(result.source_type, "quote")

    def test_core_insert_adds_active_memory_to_index(self):
        core = InMemoryMemoryCore()
        inserted = core.insert(sample_insert_card())

        index = core.index()

        self.assertEqual(inserted.status, MemoryStatus.ACTIVE)
        self.assertEqual([item.memory_id for item in index], ["mem_ritual_moon"])
        self.assertEqual(index[0].summary, "用户希望睡前被叫“小月亮”,这是亲密关系里的睡前仪式。")

    def test_core_fetch_returns_missing_ids_explicitly(self):
        core = InMemoryMemoryCore([memory_moment_to_card(sample_legacy_memory_moment())])

        batch = core.fetch(["mem_old_advice", "mem_missing"])

        self.assertEqual([memory.memory_id for memory in batch.memories], ["mem_old_advice"])
        self.assertEqual(batch.missing_ids, ["mem_missing"])
        self.assertEqual(batch.unavailable_ids, [])

    def test_core_supersede_keeps_old_card_but_removes_it_from_default_recall(self):
        old_card = memory_moment_to_card(sample_legacy_memory_moment(), salience=Salience.HIGH)
        core = InMemoryMemoryCore([old_card])

        new_card = core.supersede("mem_old_advice", sample_superseding_card())

        active_ids = [item.memory_id for item in core.index()]
        old_default_fetch = core.fetch(["mem_old_advice"])
        old_explicit_fetch = core.fetch(
            ["mem_old_advice"],
            include_statuses=(MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED),
        )

        self.assertEqual(new_card.supersedes, ["mem_old_advice"])
        self.assertEqual(active_ids, ["mem_new_advice_boundary"])
        self.assertEqual(old_default_fetch.memories, [])
        self.assertEqual(old_default_fetch.unavailable_ids, ["mem_old_advice"])
        self.assertEqual(old_explicit_fetch.memories[0].superseded_by, "mem_new_advice_boundary")

    def test_sensitive_scope_stays_out_of_index_but_is_available_in_fetch(self):
        core = InMemoryMemoryCore(
            [
                MemoryCard(
                    id="mem_sensitive_boundary",
                    summary="用户只希望记录亲密互动边界,不要记录露骨细节。",
                    verbatim="具体内容别存,你记住边界就好。",
                    bucket_refs=["亲密边界"],
                    source_type="quote",
                    salience=Salience.HIGH,
                    sensitive_scope="adult_preference_boundary",
                )
            ]
        )

        index_item = core.index()[0]
        fetch_item = core.fetch(["mem_sensitive_boundary"]).memories[0]

        self.assertFalse(hasattr(index_item, "sensitive_scope"))
        self.assertEqual(fetch_item.sensitive_scope, "adult_preference_boundary")

    def test_demo_payload_contains_full_minimum_flow(self):
        payload = build_demo_payload()

        self.assertEqual(payload["legacy_memory_moment"]["id"], "mem_old_advice")
        self.assertEqual(payload["initial_readside"]["index"][0]["memory_id"], "mem_old_advice")
        self.assertEqual(payload["writeside"]["inserted"]["id"], "mem_ritual_moon")
        self.assertEqual(payload["writeside"]["superseding"]["supersedes"], ["mem_old_advice"])
        self.assertEqual(payload["after_commit"]["old_fetch_default"]["unavailable_ids"], ["mem_old_advice"])
        self.assertNotIn("verbatim", payload["after_commit"]["active_index"][0])


if __name__ == "__main__":
    unittest.main()
